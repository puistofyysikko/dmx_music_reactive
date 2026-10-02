"""A single DMX universe: 512 channel slots, addressed 1..512."""

UNIVERSE_SIZE = 512


class Universe:
    def __init__(self) -> None:
        self._data = bytearray(UNIVERSE_SIZE)

    def set(self, address: int, value: int) -> None:
        """Set one channel (1-based address). Values are clamped to 0..255."""
        if not 1 <= address <= UNIVERSE_SIZE:
            raise ValueError(f"DMX address out of range: {address}")
        self._data[address - 1] = max(0, min(255, int(value)))

    def get(self, address: int) -> int:
        if not 1 <= address <= UNIVERSE_SIZE:
            raise ValueError(f"DMX address out of range: {address}")
        return self._data[address - 1]

    def clear(self) -> None:
        self._data[:] = bytes(UNIVERSE_SIZE)

    def as_bytes(self) -> bytes:
        return bytes(self._data)
