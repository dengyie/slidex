"""wheel 构建钩子：把 slidex/stealth.py 排除在分发之外。

stealth.py（XianyuSliderStealth，12k 行闲鱼专用链路）只被 tests/demo 引用，
服务运行时不 import——wheel 里带它纯属分发面浪费。setuptools 没有包内单
模块的 exclude 开关，这里在 build_py 阶段过滤；源码仓保留以维持测试。
"""
from setuptools import setup
from setuptools.command.build_py import build_py as _build_py


class _BuildPyExcludeStealth(_build_py):
    _EXCLUDED_MODULES = {"slidex.stealth"}

    def find_package_modules(self, package, package_dir):
        modules = super().find_package_modules(package, package_dir)
        return [
            (pkg, mod, path)
            for (pkg, mod, path) in modules
            if f"{pkg}.{mod}" not in self._EXCLUDED_MODULES
        ]


setup(cmdclass={"build_py": _BuildPyExcludeStealth})
