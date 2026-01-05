# Contributing to DAKB

Thank you for your interest in contributing to DAKB (Distributed Agent Knowledge Base)! This document provides guidelines and information for contributors.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [How to Contribute](#how-to-contribute)
- [Pull Request Process](#pull-request-process)
- [Coding Standards](#coding-standards)
- [Testing Guidelines](#testing-guidelines)
- [Documentation](#documentation)
- [Community](#community)

---

## Code of Conduct

This project adheres to a Code of Conduct that all contributors are expected to follow:

- **Be Respectful**: Treat everyone with respect and consideration
- **Be Inclusive**: Welcome contributors from all backgrounds
- **Be Constructive**: Provide helpful feedback and accept criticism gracefully
- **Be Professional**: Focus on what's best for the community and project

---

## Getting Started

### Prerequisites

- Python 3.10 or higher
- MongoDB 5.0+ (local or Docker)
- Git

### Fork and Clone

1. Fork the repository on GitHub
2. Clone your fork locally:

```bash
git clone https://github.com/YOUR_USERNAME/dakb.git
cd dakb
```

3. Add the upstream remote:

```bash
git remote add upstream https://github.com/ORIGINAL_OWNER/dakb.git
```

---

## Development Setup

### 1. Create Virtual Environment

```bash
python -m venv venv
source venv/bin/activate  # Linux/macOS
# or
venv\Scripts\activate     # Windows
```

### 2. Install Dependencies

```bash
# Install in development mode with dev dependencies
pip install -e ".[dev]"

# Or install from requirements
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

### 3. Set Up MongoDB

**Option A: Docker (Recommended)**
```bash
docker run -d -p 27017:27017 --name dakb-mongo mongo:7.0
```

**Option B: Local MongoDB**
```bash
mongod --dbpath /path/to/data
```

### 4. Configure Environment

```bash
# Copy example config
cp config/default.yaml config/local.yaml

# Edit config/local.yaml with your settings
# At minimum, verify MongoDB connection settings
```

### 5. Run Services

```bash
# Start embedding service
python -m dakb.embeddings &

# Start gateway
python -m dakb.gateway

# Or use the startup script
./scripts/start_dakb.sh
```

### 6. Verify Setup

```bash
# Health check
curl http://localhost:3100/health

# Run tests
pytest tests/
```

---

## How to Contribute

### Reporting Bugs

1. Check existing issues to avoid duplicates
2. Use the bug report template
3. Include:
   - Clear description of the bug
   - Steps to reproduce
   - Expected vs actual behavior
   - Environment details (OS, Python version, etc.)
   - Relevant logs or error messages

### Suggesting Features

1. Check existing issues and discussions
2. Use the feature request template
3. Include:
   - Clear description of the feature
   - Use case / motivation
   - Proposed implementation (if any)
   - Potential alternatives considered

### Code Contributions

1. **Find an Issue**: Look for issues labeled `good first issue` or `help wanted`
2. **Discuss**: Comment on the issue to express interest and discuss approach
3. **Branch**: Create a feature branch from `main`
4. **Implement**: Write code following our coding standards
5. **Test**: Add tests for new functionality
6. **Document**: Update relevant documentation
7. **PR**: Submit a pull request

---

## Pull Request Process

### Before Submitting

1. **Sync with upstream**:
   ```bash
   git fetch upstream
   git rebase upstream/main
   ```

2. **Run tests**:
   ```bash
   pytest tests/
   ```

3. **Run linting**:
   ```bash
   ruff check dakb/
   mypy dakb/
   ```

4. **Format code**:
   ```bash
   ruff format dakb/
   ```

### PR Guidelines

1. **Title**: Use a clear, descriptive title
   - Format: `[TYPE] Brief description`
   - Types: `[FEATURE]`, `[FIX]`, `[DOCS]`, `[REFACTOR]`, `[TEST]`

2. **Description**: Include:
   - What changes were made
   - Why the changes were needed
   - How to test the changes
   - Related issue numbers

3. **Size**: Keep PRs focused and reasonably sized
   - Large changes should be split into smaller PRs
   - Each PR should do one thing well

4. **Commits**: Write clear commit messages
   - Use present tense ("Add feature" not "Added feature")
   - First line: 50 chars max, summary
   - Body: Explain what and why (not how)

### Review Process

1. All PRs require at least one review
2. Address review feedback promptly
3. Re-request review after making changes
4. Squash commits before merging (if requested)

---

## Coding Standards

### Python Style

We follow [PEP 8](https://pep8.org/) with some modifications:

- **Line Length**: 100 characters max
- **Imports**: Sorted with `isort`, grouped (stdlib, third-party, local)
- **Formatting**: Use `ruff format`
- **Type Hints**: Required for all public functions

### Code Quality Tools

```bash
# Linting
ruff check dakb/

# Type checking
mypy dakb/

# Formatting
ruff format dakb/

# All checks
./scripts/lint.sh
```

### Project Structure

```
dakb/
├── dakb/                    # Main package
│   ├── __init__.py         # Package exports
│   ├── gateway/            # FastAPI REST API
│   │   ├── main.py        # App entry point
│   │   ├── routes/        # API routes
│   │   └── middleware/    # Auth, CORS, etc.
│   ├── db/                 # Database layer
│   │   ├── schemas.py     # Pydantic models
│   │   └── collections.py # MongoDB operations
│   ├── embeddings/         # Vector service
│   ├── mcp/                # MCP server
│   ├── messaging/          # Message system
│   └── sessions/           # Session management
├── tests/                   # Test suite
├── docs/                    # Documentation
└── examples/                # Usage examples
```

### Naming Conventions

| Type | Convention | Example |
|------|------------|---------|
| Modules | snake_case | `knowledge_service.py` |
| Classes | PascalCase | `KnowledgeEntry` |
| Functions | snake_case | `store_knowledge()` |
| Constants | UPPER_SNAKE | `MAX_RESULTS` |
| Private | _prefix | `_internal_helper()` |

---

## Testing Guidelines

### Test Structure

```
tests/
├── unit/                    # Unit tests
│   ├── test_schemas.py
│   ├── test_knowledge.py
│   └── test_messaging.py
├── integration/             # Integration tests
│   ├── test_api.py
│   └── test_mcp.py
└── conftest.py              # Shared fixtures
```

### Writing Tests

```python
import pytest
from dakb.db.schemas import KnowledgeCreate

class TestKnowledgeCreate:
    """Tests for KnowledgeCreate schema."""

    def test_valid_creation(self):
        """Test creating a valid knowledge entry."""
        knowledge = KnowledgeCreate(
            title="Test Entry",
            content="Test content",
            content_type="lesson_learned",
            category="general"
        )
        assert knowledge.title == "Test Entry"

    def test_invalid_category_raises_error(self):
        """Test that invalid category raises validation error."""
        with pytest.raises(ValueError):
            KnowledgeCreate(
                title="Test",
                content="Test",
                content_type="lesson_learned",
                category="invalid"
            )
```

### Running Tests

```bash
# All tests
pytest tests/

# Specific test file
pytest tests/unit/test_schemas.py

# With coverage
pytest tests/ --cov=dakb --cov-report=html

# Verbose output
pytest tests/ -v

# Stop on first failure
pytest tests/ -x
```

### Test Requirements

- All new features must have tests
- Bug fixes should include regression tests
- Maintain or improve code coverage
- Tests must pass in CI before merge

---

## Documentation

### Types of Documentation

1. **Code Docstrings**: All public modules, classes, and functions
2. **README**: Project overview and quick start
3. **API Reference**: REST endpoints and SDK methods
4. **Guides**: How-to guides and tutorials
5. **Architecture**: Design decisions and system overview

### Docstring Format

We use Google-style docstrings:

```python
def store_knowledge(
    title: str,
    content: str,
    category: str,
) -> KnowledgeEntry:
    """Store a new knowledge entry in the database.

    Args:
        title: Brief title for the knowledge entry (max 100 chars).
        content: The knowledge content in markdown format.
        category: Category for organization (e.g., "ml", "backend").

    Returns:
        The created KnowledgeEntry with generated ID and timestamps.

    Raises:
        ValidationError: If input validation fails.
        DatabaseError: If database operation fails.

    Example:
        >>> entry = store_knowledge(
        ...     title="CUDA OOM Fix",
        ...     content="Reduce batch size...",
        ...     category="ml"
        ... )
        >>> print(entry.knowledge_id)
        'kn_20240101_abc123'
    """
```

### Building Documentation

```bash
# Build docs locally
cd docs
mkdocs build

# Serve docs locally
mkdocs serve
```

---

## Community

### Getting Help

- **GitHub Issues**: Bug reports and feature requests
- **GitHub Discussions**: Questions and general discussion
- **Discord**: Real-time chat (link in README)

### Recognition

Contributors are recognized in:
- `CONTRIBUTORS.md` file
- Release notes
- Project README (major contributors)

---

## License

By contributing to DAKB, you agree that your contributions will be licensed under the Apache License 2.0.

---

Thank you for contributing to DAKB!
