import unittest
from minerva.core.sqlite_http import SQLiteHTTP


class TestSQLiteHTTPVarint(unittest.TestCase):
    def setUp(self):
        # Instantiate without network by overriding __init__
        self.reader = object.__new__(SQLiteHTTP)

    def test_read_single_byte_varint(self):
        val, offset = self.reader._read_varint(b"\x05\x00", 0)
        self.assertEqual(val, 5)
        self.assertEqual(offset, 1)

    def test_read_multi_byte_varint(self):
        # 0x81, 0x00 -> (1 << 7) | 0 = 128
        val, offset = self.reader._read_varint(b"\x81\x00", 0)
        self.assertEqual(val, 128)
        self.assertEqual(offset, 2)

    def test_read_varint_overflow_error(self):
        with self.assertRaises(ValueError):
            self.reader._read_varint(b"\x81\x82", 0)

    def test_parse_record_basic_types(self):
        # header_size (varint=3), type1=8 (0), type2=9 (1) -> values: [0, 1]
        data = b"\x03\x08\x09"
        parsed = self.reader._parse_record(data, 0)
        self.assertEqual(parsed, [0, 1])

    def test_parse_record_strings(self):
        # type 13 + 2*N -> string of length N
        # string "ROM" length 3 -> serial_type = 13 + 6 = 19 (0x13)
        header = b"\x02\x13"
        payload = header + b"ROM"
        parsed = self.reader._parse_record(payload, 0)
        self.assertEqual(parsed, ["ROM"])


if __name__ == "__main__":
    unittest.main()
