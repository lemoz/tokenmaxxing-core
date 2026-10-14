"""Explicit file grants and private store permissions. No discovery or chmod."""

from contextlib import contextmanager
import os
from pathlib import Path
import stat

from .normalize import signature
from .records import NormalizationError

REPOSITORY = Path(__file__).resolve().parent.parent


def absolute(value):
    if not isinstance(value, (str, Path)) or not str(value) or "\x00" in str(value):
        raise NormalizationError("invalid_absolute_path")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise NormalizationError("invalid_absolute_path")
    return path


def no_links(path):
    path = absolute(path)
    for item in [*reversed(path.parents), path]:
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise NormalizationError("symlink_not_allowed")
    return path


def regular(info):
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise NormalizationError("regular_unlinked_file_required")


class Scope:
    def __init__(self, grants=()):
        self.grants = []
        for value in grants:
            path = no_links(value)
            info = path.stat()
            if not stat.S_ISDIR(info.st_mode):
                regular(info)
            self.grants.append((path, stat.S_ISDIR(info.st_mode), (info.st_dev, info.st_ino)))

    def selected(self, value):
        path = absolute(value)
        for root, directory, identity in self.grants:
            if path == root or directory and root in path.parents:
                no_links(root)
                info = root.stat()
                if (info.st_dev, info.st_ino) != identity:
                    raise NormalizationError("allowed_root_changed")
                return path
        raise NormalizationError("path_outside_allowed_scope")

    @contextmanager
    def open(self, value):
        path = self.selected(value)
        # Walk from / with directory descriptors. O_NOFOLLOW applies at every
        # component, so a substituted symlink never expands the grant.
        descriptors = []
        try:
            directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
            descriptors.append(directory)
            for part in path.parts[1:-1]:
                directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                descriptors.append(directory)
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
            with os.fdopen(descriptor, "rb") as stream:
                regular(os.fstat(stream.fileno()))
                self.selected(value)
                yield stream
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    def verify(self, path, initial):
        with self.open(path) as current:
            if signature(initial) != signature(os.fstat(current.fileno())):
                raise NormalizationError("source_changed_during_import")


def private_directory(value, *, create=True):
    path = absolute(value)
    if path == REPOSITORY or REPOSITORY in path.parents:
        raise NormalizationError("private_store_must_be_outside_repository")
    no_links(path.parent)
    if not path.exists() and not path.is_symlink():
        if not create:
            raise NormalizationError("private_store_directory_missing")
        path.mkdir(mode=0o700)
    no_links(path)
    info = path.stat()
    if (not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700
            or info.st_uid != os.getuid()):
        raise NormalizationError("private_directory_0700_required")
    return path


def check_private_file(path):
    no_links(path)
    info = path.stat()
    regular(info)
    if stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
        raise NormalizationError("private_file_0600_required")
