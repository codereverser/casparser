import pytest

# Rewrite asserts in base test class
pytest.register_assert_rewrite("tests.base")
