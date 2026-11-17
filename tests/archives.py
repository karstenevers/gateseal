"""Release archives, built in memory as a release binary's upstream publishes them."""

import gzip
import io
import tarfile


def tar_gz(*members: tuple[str, bytes]) -> bytes:
    """Return a tar.gz of each named member, in order, with fixed times and owners.

    A name that ends in / is a folder.
    """
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as bundle:
        for name, data in members:
            info = tarfile.TarInfo(name.rstrip("/"))
            info.mode = 0o755
            if name.endswith("/"):
                info.type = tarfile.DIRTYPE
            else:
                info.size = len(data)
            bundle.addfile(info, io.BytesIO(data))
    return gzip.compress(buffer.getvalue(), mtime=0)
