"""验证本地 HTTP 服务只选择完整的 Vite 前端构建目录。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from evalhub.server import frontend_directory


class FrontendDirectoryTests(unittest.TestCase):
    """保护前端构建目录发现与缺失构建时的诊断行为。"""

    def test_uses_vite_dist_directory(self) -> None:
        """存在入口文件时应返回项目根目录下的 ``frontend/dist``。"""
        # 临时目录完整模拟项目结构，避免测试依赖仓库当前是否执行过前端构建。
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dist = root / "frontend" / "dist"
            dist.mkdir(parents=True)
            # 入口文件是构建完成的最小判据，正文内容本身不影响目录选择。
            (dist / "index.html").write_text("<div id='root'></div>", encoding="utf-8")

            # 返回路径必须精确指向 dist，不能回退到包含 TypeScript 源码的 frontend 根目录。
            self.assertEqual(frontend_directory(root), dist)

    def test_requires_a_built_frontend(self) -> None:
        """缺少构建入口时应提示执行明确的 npm 构建命令。"""
        # 空临时项目代表首次启动但尚未构建 React 控制台的环境。
        with TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(FileNotFoundError, "npm --prefix frontend run build"):
                frontend_directory(Path(temp_dir))


if __name__ == "__main__":
    # 支持直接运行该文件，快速验证本地静态资源目录约束。
    unittest.main()
