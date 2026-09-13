from src.hello import hello


def test_hello():
    """
    最简单的测试用例。
    验证 hello() 函数返回 "Hello"。
    """
    assert hello() == "Hello"
