"""
真实验证码测试 - 验证 provider 自动检测和求解

使用方式:
    PYTHONPATH=. python3 tests/manual/test_real_captcha.py [url]

示例:
    # GeeTest demo
    PYTHONPATH=. python3 tests/manual/test_real_captcha.py https://www.geetest.com/demo/

    # Aliyun demo
    PYTHONPATH=. python3 tests/manual/test_real_captcha.py https://promotion.aliyun.com/ntms/act/captchaIntroAndDemo.html

功能:
    - 自动检测验证码供应商 (provider="auto")
    - 尝试求解滑块验证码
    - 输出详细日志和诊断信息
"""

import asyncio
import sys
from pathlib import Path

from loguru import logger
from slidex import SliderSolver


async def test_real_captcha(url: str):
    """测试真实验证码"""
    print("\n" + "="*70)
    print(f"测试 URL: {url}")
    print("="*70)

    # 配置日志级别为 DEBUG
    logger.remove()
    logger.add(
        sys.stderr,
        level="DEBUG",
        format="<green>{time:HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
    )

    # 创建 solver (auto 模式)
    solver = SliderSolver(
        cookie_id="test_user",
        provider="auto",  # 自动检测
        headless=False,  # 显示浏览器便于观察
    )

    print("\n✓ SliderSolver 创建成功 (provider=auto, headless=False)")
    print(f"✓ 已注册的 provider: {SliderSolver.list_providers()}\n")

    try:
        print("➤ 开始求解...")
        success, cookies = await solver.solve(url, max_attempts=1)

        print("\n" + "="*70)
        if success:
            print("✅ 验证码求解成功！")
            print(f"✓ 获取到 {len(cookies) if cookies else 0} 个 cookies")
            if cookies:
                for name, value in list(cookies.items())[:3]:
                    print(f"  - {name}: {value[:50]}...")
        else:
            print("❌ 验证码求解失败")
            print("可能原因:")
            print("  1. provider 自动检测失败 (不支持该供应商)")
            print("  2. 页面结构变化")
            print("  3. 图像匹配失败")
            print("  4. 网络问题")
        print("="*70)

    except KeyboardInterrupt:
        print("\n⚠ 用户中断测试")
    except Exception as e:
        print("\n" + "="*70)
        print(f"❌ 测试异常: {e}")
        print("="*70)
        import traceback
        traceback.print_exc()
    finally:
        await solver.close()
        print("\n✓ 浏览器已关闭")


async def interactive_test():
    """交互式测试"""
    print("\n" + "="*70)
    print("Slidex 真实验证码测试工具")
    print("="*70)

    test_urls = {
        "1": ("GeeTest Demo", "https://www.geetest.com/demo/"),
        "2": ("GeeTest Adaptive", "https://www.geetest.com/adaptive-captcha-demo"),
        "3": ("Aliyun Demo", "https://promotion.aliyun.com/ntms/act/captchaIntroAndDemo.html"),
    }

    print("\n可选测试站点:")
    for key, (name, url) in test_urls.items():
        print(f"  {key}. {name}")
        print(f"     {url}")

    print("\n  0. 自定义 URL")
    print()

    choice = input("请选择 [0-3]: ").strip()

    if choice in test_urls:
        name, url = test_urls[choice]
        print(f"\n✓ 已选择: {name}")
        await test_real_captcha(url)
    elif choice == "0":
        url = input("请输入 URL: ").strip()
        if url:
            await test_real_captcha(url)
        else:
            print("❌ URL 不能为空")
    else:
        print("❌ 无效选择")


def main():
    """主入口"""
    if len(sys.argv) > 1:
        # 命令行模式
        url = sys.argv[1]
        asyncio.run(test_real_captcha(url))
    else:
        # 交互式模式
        asyncio.run(interactive_test())


if __name__ == "__main__":
    main()
