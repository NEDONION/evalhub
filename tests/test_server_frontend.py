from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from evalhub.server import frontend_directory


class FrontendDirectoryTests(unittest.TestCase):
    def test_uses_vite_dist_directory(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dist = root / "frontend" / "dist"
            dist.mkdir(parents=True)
            (dist / "index.html").write_text("<div id='root'></div>", encoding="utf-8")

            self.assertEqual(frontend_directory(root), dist)

    def test_requires_a_built_frontend(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(FileNotFoundError, "npm --prefix frontend run build"):
                frontend_directory(Path(temp_dir))


if __name__ == "__main__":
    unittest.main()
