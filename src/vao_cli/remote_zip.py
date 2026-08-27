from __future__ import annotations

import binascii
import stat
import struct
import unicodedata
import zlib
from dataclasses import dataclass
from pathlib import PurePosixPath

from .errors import IntegrityError, UnsupportedError
from .http import HTTPClient

EOCD_SIGNATURE = b"PK\x05\x06"
ZIP64_LOCATOR_SIGNATURE = b"PK\x06\x07"
ZIP64_EOCD_SIGNATURE = b"PK\x06\x06"
CENTRAL_SIGNATURE = b"PK\x01\x02"
LOCAL_SIGNATURE = b"PK\x03\x04"
MAX_PATH_SEGMENTS = 128
MAX_TOTAL_BYTES = 4 * 1024**4
MAX_COMPRESSION_RATIO = 1_000
RATIO_CHECK_MIN_BYTES = 64 * 1024 * 1024


def _safe_path(value: str) -> bool:
    path = PurePosixPath(value)
    components = (value.removesuffix("/")).split("/")
    return (
        bool(value.rstrip("/"))
        and not path.is_absolute()
        and "\\" not in value
        and "\x00" not in value
        and not any(
            ord(character) < 0x20 or ord(character) == 0x7F for character in value
        )
        and all(part not in {"", ".", ".."} for part in components)
        and len(path.parts) <= MAX_PATH_SEGMENTS
    )


@dataclass(frozen=True)
class RemoteZipEntry:
    name: str
    flags: int
    compression: int
    crc32: int
    compressed_size: int
    uncompressed_size: int
    local_header_offset: int
    external_attributes: int

    @property
    def is_directory(self) -> bool:
        return self.name.endswith("/")


class RemoteZipReader:
    """Bounded ZIP/ZIP64 reader backed only by single HTTP byte ranges."""

    maximum_entries = 100_000
    maximum_central_directory = 256 * 1024 * 1024
    maximum_entry = 1024 * 1024 * 1024 * 1024

    def __init__(
        self,
        http: HTTPClient,
        url: str,
        size: int,
        *,
        require_vao_layout: bool = True,
    ):
        if size < 22:
            raise IntegrityError("Remote file is too small to be ZIP")
        self.http = http
        self.url = url
        self.size = size
        self.require_vao_layout = require_vao_layout
        self.entries = self._read_directory()
        self._by_name = {entry.name: entry for entry in self.entries}
        if require_vao_layout and (
            not self.entries
            or self.entries[0].name != "mimetype"
            or self.entries[0].compression != 0
        ):
            raise IntegrityError("mimetype must be the first stored ZIP entry")

    def entry(self, name: str) -> RemoteZipEntry | None:
        return self._by_name.get(name)

    def require_entry(self, name: str) -> RemoteZipEntry:
        result = self.entry(name)
        if result is None:
            raise IntegrityError(f"Remote VAO lacks {name}")
        return result

    def read(self, entry: RemoteZipEntry, *, maximum: int) -> bytes:
        if entry.is_directory:
            return b""
        if entry.uncompressed_size > maximum:
            raise IntegrityError(
                f"Entry {entry.name!r} exceeds the {maximum}-byte limit"
            )
        data_offset = self.data_offset(entry)
        if entry.compression == 8 and entry.compressed_size > (
            maximum + maximum // 1000 + 65_536
        ):
            raise IntegrityError(
                f"Compressed entry {entry.name!r} exceeds its bounded read budget"
            )
        if entry.compressed_size == 0:
            compressed = b""
        else:
            compressed, _ = self.http.get_range(
                self.url, data_offset, data_offset + entry.compressed_size - 1
            )
        if entry.compression == 0:
            data = compressed
        elif entry.compression == 8:
            try:
                decoder = zlib.decompressobj(-15)
                data = decoder.decompress(compressed, maximum + 1)
                if decoder.unconsumed_tail or len(data) > maximum:
                    raise IntegrityError(
                        f"Deflate stream for {entry.name!r} exceeds its read limit"
                    )
                data += decoder.flush()
            except zlib.error as exc:
                raise IntegrityError(
                    f"Deflate stream for {entry.name!r} is invalid: {exc}"
                ) from exc
        else:
            raise UnsupportedError(
                f"ZIP compression method {entry.compression} for {entry.name!r} is unsupported"
            )
        if len(data) != entry.uncompressed_size or len(data) > maximum:
            raise IntegrityError(f"Uncompressed size mismatch for {entry.name!r}")
        if binascii.crc32(data) & 0xFFFFFFFF != entry.crc32:
            raise IntegrityError(f"ZIP CRC-32 mismatch for {entry.name!r}")
        return data

    def data_offset(self, entry: RemoteZipEntry) -> int:
        raw, _ = self.http.get_range(
            self.url, entry.local_header_offset, entry.local_header_offset + 29
        )
        values = struct.unpack("<4s5H3I2H", raw)
        if values[0] != LOCAL_SIGNATURE:
            raise IntegrityError(f"Invalid local ZIP header for {entry.name!r}")
        flags, compression, name_length, extra_length = (
            values[2],
            values[3],
            values[-2],
            values[-1],
        )
        if flags & 0x1:
            raise IntegrityError(f"Encrypted ZIP entry is prohibited: {entry.name!r}")
        if compression != entry.compression:
            raise IntegrityError(
                f"ZIP compression metadata disagrees for {entry.name!r}"
            )
        header_tail_length = name_length + extra_length
        if header_tail_length:
            header_tail, _ = self.http.get_range(
                self.url,
                entry.local_header_offset + 30,
                entry.local_header_offset + 30 + header_tail_length - 1,
            )
            try:
                local_name = header_tail[:name_length].decode("utf-8")
            except UnicodeDecodeError as exc:
                raise IntegrityError("ZIP local path is not UTF-8") from exc
            if local_name != entry.name:
                raise IntegrityError(
                    f"Local and central ZIP names disagree for {entry.name!r}"
                )
        offset = entry.local_header_offset + 30 + name_length + extra_length
        if offset < 0 or offset + entry.compressed_size > self.size:
            raise IntegrityError(f"ZIP data range is invalid for {entry.name!r}")
        return offset

    def _read_directory(self) -> list[RemoteZipEntry]:
        tail_size = min(self.size, 65_557)
        tail_start = self.size - tail_size
        tail, _ = self.http.get_range(self.url, tail_start, self.size - 1)
        relative = self._find_eocd(tail)
        absolute_eocd = tail_start + relative
        eocd = tail[relative : relative + 22]
        (
            signature,
            disk,
            directory_disk,
            disk_count,
            total_count,
            central_size,
            central_offset,
            _,
        ) = struct.unpack("<4s4H2IH", eocd)
        if (
            signature != EOCD_SIGNATURE
            or disk != 0
            or directory_disk != 0
            or disk_count != total_count
        ):
            raise UnsupportedError("Multi-disk ZIP archives are unsupported")
        if (
            total_count == 0xFFFF
            or central_size == 0xFFFFFFFF
            or central_offset == 0xFFFFFFFF
        ):
            total_count, central_size, central_offset = self._read_zip64(absolute_eocd)
        if total_count > self.maximum_entries:
            raise IntegrityError("ZIP contains too many entries")
        if central_size > self.maximum_central_directory:
            raise IntegrityError("ZIP central directory exceeds the safety limit")
        if (
            central_offset < 0
            or central_size < 0
            or central_offset + central_size > self.size
        ):
            raise IntegrityError("ZIP central directory range is invalid")
        if central_size == 0:
            central = b""
        elif (
            central_offset >= tail_start and central_offset + central_size <= self.size
        ):
            start = central_offset - tail_start
            central = tail[start : start + central_size]
        else:
            central, _ = self.http.get_range(
                self.url, central_offset, central_offset + central_size - 1
            )
        result = self._parse_central(central)
        if len(result) != total_count:
            raise IntegrityError(
                f"ZIP directory declares {total_count} entries but contains {len(result)}"
            )
        normalized: dict[str, str] = {}
        portable: dict[str, str] = {}
        total = 0
        allowed_exact = {"mimetype", "vao-manifest.json", "META-INF/vao-carrier.json"}
        for entry in result:
            canonical = unicodedata.normalize("NFC", entry.name)
            if canonical in normalized:
                raise IntegrityError("ZIP contains duplicate or NFC-equivalent paths")
            normalized[canonical] = entry.name
            folded = canonical.casefold()
            if folded in portable and portable[folded] != entry.name:
                raise IntegrityError(
                    "ZIP contains paths that collide after NFC/case-fold normalization"
                )
            portable[folded] = entry.name
            if self.require_vao_layout:
                if entry.is_directory:
                    if not entry.name.startswith("payload/"):
                        raise IntegrityError(
                            f"Unknown VAO carrier directory {entry.name!r}"
                        )
                elif entry.name not in allowed_exact and not entry.name.startswith(
                    "payload/"
                ):
                    raise IntegrityError(f"Unknown VAO carrier entry {entry.name!r}")
            total += entry.uncompressed_size
        if total > MAX_TOTAL_BYTES:
            raise IntegrityError("ZIP exceeds the total uncompressed-size limit")
        missing = allowed_exact - set(normalized.values())
        if self.require_vao_layout and missing:
            raise IntegrityError(
                "ZIP lacks required VAO entries: " + ", ".join(sorted(missing))
            )
        return result

    @staticmethod
    def _find_eocd(tail: bytes) -> int:
        for position in range(len(tail) - 22, -1, -1):
            if tail[position : position + 4] != EOCD_SIGNATURE:
                continue
            comment_length = struct.unpack_from("<H", tail, position + 20)[0]
            if position + 22 + comment_length == len(tail):
                return position
        raise IntegrityError("ZIP end-of-central-directory record is missing")

    def _read_zip64(self, absolute_eocd: int) -> tuple[int, int, int]:
        if absolute_eocd < 20:
            raise IntegrityError("ZIP64 locator is missing")
        locator, _ = self.http.get_range(
            self.url, absolute_eocd - 20, absolute_eocd - 1
        )
        signature, disk, record_offset, disk_count = struct.unpack("<4sIQI", locator)
        if signature != ZIP64_LOCATOR_SIGNATURE or disk != 0 or disk_count != 1:
            raise UnsupportedError("Invalid or multi-disk ZIP64 locator")
        header, _ = self.http.get_range(self.url, record_offset, record_offset + 55)
        if header[:4] != ZIP64_EOCD_SIGNATURE:
            raise IntegrityError("ZIP64 end record is invalid")
        values = struct.unpack("<4sQ2H2I4Q", header)
        disk, directory_disk = values[4], values[5]
        disk_count, total_count, central_size, central_offset = (
            values[6],
            values[7],
            values[8],
            values[9],
        )
        if disk != 0 or directory_disk != 0 or disk_count != total_count:
            raise UnsupportedError("Multi-disk ZIP64 archives are unsupported")
        return int(total_count), int(central_size), int(central_offset)

    def _parse_central(self, data: bytes) -> list[RemoteZipEntry]:
        result: list[RemoteZipEntry] = []
        cursor = 0
        while cursor < len(data):
            if cursor + 46 > len(data):
                raise IntegrityError("Truncated ZIP central directory record")
            values = struct.unpack_from("<4s6H3I5H2I", data, cursor)
            if values[0] != CENTRAL_SIGNATURE:
                raise IntegrityError("Invalid ZIP central directory signature")
            flags, compression = values[3], values[4]
            crc32, compressed32, uncompressed32 = values[7], values[8], values[9]
            name_length, extra_length, comment_length = (
                values[10],
                values[11],
                values[12],
            )
            external_attributes, offset32 = values[15], values[16]
            end = cursor + 46 + name_length + extra_length + comment_length
            if end > len(data):
                raise IntegrityError("Truncated ZIP central directory metadata")
            name_raw = data[cursor + 46 : cursor + 46 + name_length]
            extra = data[
                cursor + 46 + name_length : cursor + 46 + name_length + extra_length
            ]
            try:
                name = name_raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise IntegrityError("ZIP path is not UTF-8") from exc
            if not _safe_path(name):
                raise IntegrityError(f"Unsafe ZIP path {name!r}")
            if flags & 0x1:
                raise IntegrityError(f"Encrypted ZIP entry is prohibited: {name!r}")
            compressed, uncompressed, offset = (
                int(compressed32),
                int(uncompressed32),
                int(offset32),
            )
            if (
                compressed32 == 0xFFFFFFFF
                or uncompressed32 == 0xFFFFFFFF
                or offset32 == 0xFFFFFFFF
            ):
                compressed, uncompressed, offset = self._zip64_entry_values(
                    extra, compressed32, uncompressed32, offset32
                )
            if uncompressed > self.maximum_entry or compressed > self.size:
                raise IntegrityError(f"ZIP entry exceeds safety limits: {name!r}")
            if compression not in {0, 8}:
                raise UnsupportedError(
                    f"ZIP compression method {compression} for {name!r} is unsupported"
                )
            if compression == 0 and uncompressed != compressed:
                raise IntegrityError(
                    f"Stored ZIP entry has inconsistent sizes: {name!r}"
                )
            if compression == 8 and uncompressed > 0 and compressed == 0:
                raise IntegrityError(
                    f"Deflated ZIP entry has an impossible zero size: {name!r}"
                )
            if (
                uncompressed >= RATIO_CHECK_MIN_BYTES
                and compressed
                and uncompressed / compressed > MAX_COMPRESSION_RATIO
            ):
                raise IntegrityError(
                    f"ZIP entry exceeds the compression-ratio limit: {name!r}"
                )
            file_type = (external_attributes >> 16) & 0o170000
            if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise IntegrityError(
                    f"ZIP links and special files are prohibited: {name!r}"
                )
            result.append(
                RemoteZipEntry(
                    name=name,
                    flags=flags,
                    compression=compression,
                    crc32=crc32,
                    compressed_size=compressed,
                    uncompressed_size=uncompressed,
                    local_header_offset=offset,
                    external_attributes=external_attributes,
                )
            )
            cursor = end
        return result

    @staticmethod
    def _zip64_entry_values(
        extra: bytes, compressed32: int, uncompressed32: int, offset32: int
    ) -> tuple[int, int, int]:
        cursor = 0
        payload: bytes | None = None
        while cursor + 4 <= len(extra):
            identifier, length = struct.unpack_from("<HH", extra, cursor)
            end = cursor + 4 + length
            if end > len(extra):
                raise IntegrityError("Malformed ZIP extra field")
            if identifier == 0x0001:
                payload = extra[cursor + 4 : end]
                break
            cursor = end
        if payload is None:
            raise IntegrityError("ZIP64 entry lacks its ZIP64 extra field")
        position = 0

        def take() -> int:
            nonlocal position
            if position + 8 > len(payload):
                raise IntegrityError("Truncated ZIP64 entry values")
            value = struct.unpack_from("<Q", payload, position)[0]
            position += 8
            return int(value)

        uncompressed = take() if uncompressed32 == 0xFFFFFFFF else int(uncompressed32)
        compressed = take() if compressed32 == 0xFFFFFFFF else int(compressed32)
        offset = take() if offset32 == 0xFFFFFFFF else int(offset32)
        return compressed, uncompressed, offset
