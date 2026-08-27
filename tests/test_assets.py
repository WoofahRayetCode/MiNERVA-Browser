import unittest
import pathlib
from minerva.constants import (
    get_assets_dir,
    get_icon_png_path,
    get_icon_ico_path,
)


class TestAssets(unittest.TestCase):
    def test_assets_dir_exists(self):
        assets_dir = get_assets_dir()
        self.assertTrue(assets_dir.exists(), f"Assets dir {assets_dir} does not exist")
        self.assertTrue(assets_dir.is_dir())

    def test_icon_png_exists_and_valid(self):
        png_path = get_icon_png_path()
        self.assertTrue(png_path.exists(), f"PNG icon {png_path} does not exist")
        self.assertGreater(png_path.stat().st_size, 1024)

    def test_icon_ico_exists_and_valid(self):
        ico_path = get_icon_ico_path()
        self.assertTrue(ico_path.exists(), f"ICO icon {ico_path} does not exist")
        self.assertGreater(ico_path.stat().st_size, 1024)


if __name__ == "__main__":
    unittest.main()
