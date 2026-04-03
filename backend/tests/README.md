# 测试指南

本文档说明如何运行项目测试。

## 测试结构

```
backend/tests/
├── conftest.py              # pytest配置和fixtures
├── pytest.ini              # pytest配置
├── README.md               # 本文档
├── test_models.py          # 数据库模型测试
├── test_conversation_format.py  # 对话格式测试
├── test_context_manager.py  # 上下文管理器测试
└── __init__.py
```

## 运行测试

### 安装测试依赖

```bash
pip install -r requirements.txt
pip install pytest pytest-cov pytest-asyncio
```

### 运行所有测试

```bash
# 在backend目录下运行
pytest

# 或指定路径
pytest backend/tests/
```

### 运行特定测试文件

```bash
# 运行模型测试
pytest backend/tests/test_models.py

# 运行对话格式测试
pytest backend/tests/test_conversation_format.py

# 运行上下文管理器测试
pytest backend/tests/test_context_manager.py
```

### 运行特定测试类

```bash
# 运行Conversation模型测试
pytest backend/tests/test_models.py::TestConversationModel

# 运行Message模型测试
pytest backend/tests/test_models.py::TestMessageModel
```

### 运行特定测试方法

```bash
pytest backend/tests/test_models.py::TestTaskModel::test_task_start
```

### 运行测试并显示详细输出

```bash
pytest -v
```

### 运行测试并显示覆盖率

```bash
pytest --cov=backend
```

### 生成HTML覆盖率报告

```bash
pytest --cov=backend --cov-report=html
```

然后在 `htmlcov/index.html` 中查看详细报告。

### 运行特定标记的测试

```bash
# 只运行单元测试
pytest -m unit

# 只运行集成测试
pytest -m integration

# 跳过慢速测试
pytest -m "not slow"
```

## 测试覆盖率要求

当前项目要求测试覆盖率不低于80%。

```bash
pytest --cov=backend --cov-fail-under=80
```

## 测试最佳实践

### 1. 编写测试

- 每个功能模块都应该有对应的测试文件
- 测试类命名为 `Test{ModuleName}`
- 测试方法命名为 `test_{function_name}`
- 使用描述性的测试名称

示例:
```python
class TestTaskModel:
    def test_task_start(self, sample_task):
        """测试任务开始"""
        sample_task.start()
        assert sample_task.status == TaskStatus.RUNNING
```

### 2. 使用Fixtures

使用pytest fixtures来设置测试数据:

```python
@pytest.fixture
def sample_task():
    """示例任务数据"""
    return Task(
        name="测试任务",
        task_type=TaskType.DATA_EXPORT
    )

# 在测试中使用
def test_task_creation(sample_task):
    assert sample_task.name == "测试任务"
```

### 3. 测试数据库

- 使用内存数据库(SQLite)进行测试
- 在测试前后清理数据
- 使用独立的事务进行测试

### 4. 异步测试

对于异步代码，使用pytest-asyncio:

```python
import pytest

@pytest.mark.asyncio
async def test_async_function():
    result = await some_async_function()
    assert result == expected
```

### 5. Mock外部依赖

对于外部API调用，使用mock:

```python
from unittest.mock import Mock, patch

@patch('backend.core.model_adapters.claude.Anthropic')
async def test_claude_adapter(mock_client):
    mock_client.return_value.messages.create.return_value = Mock(
        content=[Mock(text="测试回复")],
        usage=Mock(input_tokens=10, output_tokens=20)
    )

    # 测试代码
```

## 常见问题

### Q: 测试失败 - "database is locked"

A: 确保在测试后正确关闭数据库连接:

```python
@pytest.fixture
def test_db(test_engine):
    Session = sessionmaker(bind=test_engine)
    session = Session()
    yield session
    session.close()  # 重要!
```

### Q: 异步测试不运行

A: 安装pytest-asyncio并添加装饰器:

```bash
pip install pytest-asyncio
```

```python
@pytest.mark.asyncio
async def test_async():
    await some_function()
```

### Q: 测试覆盖率低

A: 增加更多测试用例，特别关注:
- 边界条件
- 异常处理
- 错误路径

### Q: 测试运行慢

A: 使用以下优化:
- 使用内存数据库(SQLite)
- 并行运行测试: `pytest -n auto`
- 跳过慢速测试: `pytest -m "not slow"`

## 测试报告

### 查看测试覆盖率

```bash
pytest --cov=backend --cov-report=term-missing
```

### 生成HTML报告

```bash
pytest --cov=backend --cov-report=html:htmlcov
open htmlcov/index.html  # macOS/Linux
start htmlcov/index.html  # Windows
```

### 持续集成

在CI/CD中运行测试:

```yaml
# GitHub Actions示例
- name: Run tests
  run: |
    cd backend
    pip install -r requirements.txt
    pytest --cov=backend --cov-report=xml

- name: Upload coverage
  uses: codecov/codecov-action@v1
  with:
    file: ./backend/coverage.xml
```

## 参考资源

- [pytest文档](https://docs.pytest.org/)
- [pytest-cov文档](https://pytest-cov.readthedocs.io/)
- [pytest-asyncio文档](https://pytest-asyncio.readthedocs.io/)
- [SQLAlchemy测试最佳实践](https://docs.sqlalchemy.org/en/14/orm/session.html)
