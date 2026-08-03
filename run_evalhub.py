"""允许在未安装包时从仓库根目录启动 EvalHub CLI。"""

import sys
from pathlib import Path

# 在导入项目包前把 ``src`` 布局目录加入搜索路径，支持直接运行仓库脚本。
ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

# 该导入有意位于路径初始化之后，否则直接运行脚本时无法找到 ``evalhub`` 包。
from evalhub.cli import main  # noqa: E402

if __name__ == "__main__":
    # 把 CLI 返回码交给解释器，保证 shell 能准确识别命令执行结果。
    raise SystemExit(main())
