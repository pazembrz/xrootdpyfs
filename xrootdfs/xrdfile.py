# -*- coding: utf-8 -*-
#
# This file is part of xrootdfs
# Copyright (C) 2015 CERN.
#
# xrootdfs is free software; you can redistribute it and/or modify it under the
# terms of the Revised BSD License; see LICENSE file for more details.

"""Wrapper for XRootD files."""

from __future__ import absolute_import, print_function, unicode_literals

from fs import SEEK_CUR, SEEK_END, SEEK_SET
from fs.errors import InvalidPathError, PathError, ResourceNotFoundError
from XRootD.client import File

from .utils import is_valid_path, is_valid_url, spliturl, \
    translate_file_mode_to_flags


class XRootDFile(object):

    """Wrapper-like class for XRootD file objects.

    This class understands and will accept the following mode strings,
    with any additional characters being ignored:

    * ``r`` - open the file for reading only.
    * ``r+`` - open the file for reading and writing.
    * ``r-`` - open the file for streamed reading; do not allow seek/tell.
    * ``w`` - open the file for writing only; create the file if
      it doesn't exist; truncate it to zero length.
    * ``w+`` - open the file for reading and writing; create the file
      if it doesn't exist; truncate it to zero length.
    * ``w-`` - open the file for streamed writing; do not allow seek/tell.
    * ``a`` - open the file for writing only; create the file if it
      doesn't exist; place pointer at end of file.
    * ``a+`` - open the file for reading and writing; create the file
      if it doesn't exist; place pointer at end of file.
    """

    def __init__(self, path, mode='r', buffering=-1, encoding=None,
                 errors=None, newline=None, line_buffering=False, timeout=0,
                 **kwargs):
        """XRootDFile constructor.

        Raises PathError if the given path isn't a valid XRootD URL,
        and InvalidPathError if it isn't a valid XRootD file path.
        """
        if not is_valid_url(path):
            raise PathError(path)

        xpath = spliturl(path)[1]

        if not is_valid_path(xpath):
            raise InvalidPathError(xpath)

        # PyFS attributes
        self.mode = mode

        # XRootD attributes & internals
        self._file = File()
        self._ipp = 0
        self._size = -1
        self._iterator = None
        self.timeout = timeout

        # flag translation
        self._flags = translate_file_mode_to_flags(mode)

        statmsg, response = self._file.open(path, flags=self._flags,
                                            timeout=self.timeout)
        if not statmsg.ok or statmsg.error:
            if statmsg.errno == 3011:
                raise ResourceNotFoundError(path)
            else:
                raise IOError(
                    "XRootD error while instantiating file ({0}): {1}"
                    .format(path, statmsg.message))

        # Deal with the modes
        if self.mode == 'a':
            self.seek(self.size, SEEK_SET)

    def __iter__(self):
        """Initialize the internal iterator."""
        if self._iterator is None:
            self._iterator = self._file.readchunks()
        return self

    def next(self):
        """Return next item."""
        return self._iterator.next()

    def read(self, sizehint=-1):
        """Read approximately <sizehint> bytes from the file-like object.

        The method need not guarantee any particular number of bytes -
        it may return more bytes than requested, or fewer.  If needed the
        size hint may be completely ignored.  It may even return an empty
        string if no data is yet available.

        Because of this, the method must return None to signify that EOF
        has been reached.  The higher-level methods will never indicate EOF
        until None has been read from _read().  Once EOF is reached, it
        should be safe to call _read() again, immediately returning None.
        """
        if self.closed:
            raise ValueError("I/O operation on closed file.")

        self._assert_mode("r-")

        chunksize = sizehint if sizehint > 0 else self.size

        # Read data
        statmsg, res = self._file.read(
            offset=self._ipp,
            size=chunksize,
            timeout=self.timeout,
        )

        if not statmsg.ok or statmsg.error:
            raise IOError("XRootD error reading file: {0}".format(
                          statmsg.message))

        # Increment internal file pointer.
        self._ipp = min(self._ipp + chunksize, self.size)

        return res

    def readline(self, sizehint=None):
        """Read one entire line from the file.

        A trailing newline character is kept in the string (but may be absent
        when a file ends with an incomplete line). [6] If the size argument
        is present and non-negative, it is a maximum byte count (including the
        trailing newline) and an incomplete line may be returned. When size is
        not 0, an empty string is returned only when EOF is encountered
        immediately.
        """
        self._assert_mode("r-")
        return self._file.readline(chunksize=sizehint)

    def readlines(self, sizehint=None):
        """Read until EOF using readline().

        Returns a list containing the lines thus read. If the optional
        sizehint argument is present, instead of reading up to EOF, whole
        lines totalling approximately sizehint bytes (possibly after rounding
        up to an internal buffer size) are read.
        """
        self._assert_mode("r-")
        return self._file.readlines(chunksize=sizehint)

    def xreadlines(self):
        """Get an iterator over number of lines."""
        self._assert_mode("r-")
        return iter(self)

    def write(self, string, flushing=False):
        """Write the given string to the file-like object.

        If the keyword argument 'flushing' is true, it indicates that the
        internal write buffers are being flushed, and *all* the given data
        is expected to be written to the file.
        """
        self._assert_mode("w-")

        if 'a' in self.mode:
            self.seek(0, SEEK_END)

        statmsg, res = self._file.write(string, offset=self._ipp,
                                        timeout=self.timeout)

        if not statmsg.ok or statmsg.error:
            raise IOError("XRootD error writing to file: {0}".format(
                          statmsg.message))

        self._ipp += len(string)
        self._size = max(self.size, self.tell())
        if flushing:
            self.flush()

    def seek(self, offset, whence=SEEK_SET):
        """Set the file's internal position pointer, approximately.

        The possible values of whence and their meaning are defined
        in the Linux man pages for `lseek()`:
        http://man7.org/linux/man-pages/man2/lseek.2.html

        ``SEEK_SET``
            The internal position pointer is set to offset bytes.
        ``SEEK_CUR``
            The ipp is set to its current position plus offset bytes.
        ``SEEK_END``
            The ipp is set to the size of the file plus offset bytes.
        """
        if "-" in self.mode:
            raise IOError("File is not seekable.")

        if whence == SEEK_SET:
            self._ipp = offset
        elif whence == SEEK_CUR:
            self._ipp += offset
        elif whence == SEEK_END:
            self._ipp = self.size + offset
        else:
            raise NotImplementedError(whence)

    def tell(self):
        """Get the location of the file's internal position pointer."""
        return self._ipp

    def truncate(self, size):
        """Truncate the file's size to ``size``.

        Note that ``size`` will never be None; if it was not specified by the
        user then it is calculated as the file's apparent position (which may
        be different to its actual position due to buffering).
        """
        if "-" in self.mode:
            raise IOError("File is not seekable; can't truncate.")

        statmsg = self._file.truncate(size, timeout=self.timeout)[0]
        if not statmsg.ok or statmsg.error:
            raise IOError("XRootD error while truncating: {0}".format(
                          statmsg.message))

        self._ipp = 0
        self._size = size

    def close(self):
        """Close the file, including flushing the write buffers.

        The file may not be accessed further once it is closed.
        """
        if not self.closed:
            self._file.close(timeout=self.timeout)

    def flush(self):
        """Flush write buffers."""
        if not self.closed:
            statmsg = self._file.sync(timeout=self.timeout)
            if not statmsg.ok or statmsg.error:
                raise IOError("XRootD error while flushing write buffer: {0}".
                              format(statmsg.message))

    @property
    def closed(self):
        """Check if file is closed."""
        return not self._file.is_open()

    @property
    def size(self):
        """Get file size."""
        if self._size == -1:
            statmsg, res = self._file.stat(timeout=self.timeout)
            if not statmsg.ok or statmsg.error:
                raise IOError("XRootD error while retrieving size: {0}".format(
                              statmsg.message))
            self._size = res.size
        return self._size

    def _assert_mode(self, mode, mstr=None):
        """Check whether the file may be accessed in the given mode."""
        if mstr is None:
            try:
                mstr = self.mode
            except AttributeError:
                mstr = "r+"
        if "+" in mstr:
            return True
        if "-" in mstr and "-" not in mode:
            raise IOError("File does not support seeking.")
        if "r" in mode:
            if "r" not in mstr:
                raise IOError("File not opened for reading")
        if "w" in mode:
            if "w" not in mstr and "a" not in mstr:
                raise IOError("File not opened for writing")
        return True