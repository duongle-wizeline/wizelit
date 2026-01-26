#!/bin/bash

# Build Check Script for Wizelit Project
# This script validates whether the project can be built and run normally

set +e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

CHECKS_PASSED=0
CHECKS_FAILED=0

# Helper functions
print_header() {
    echo -e "\n${BLUE}=== $1 ===${NC}"
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
    ((CHECKS_PASSED++))
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
    ((CHECKS_FAILED++))
}

print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

print_info() {
    echo -e "${BLUE}ℹ $1${NC}"
}

# Check 1: Required commands
print_header "Checking Required Commands"

if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version 2>&1)
    print_success "Python 3 is installed: $PYTHON_VERSION"
else
    print_error "Python 3 is not installed"
fi

if command -v uv &> /dev/null; then
    UV_VERSION=$(uv --version)
    print_success "uv is installed: $UV_VERSION"
else
    print_error "uv is not installed (required for dependency management)"
fi

if command -v docker &> /dev/null; then
    DOCKER_VERSION=$(docker --version)
    print_success "Docker is installed: $DOCKER_VERSION"
else
    print_warning "Docker is not installed (needed for docker-compose services)"
fi

if command -v docker-compose &> /dev/null || docker compose version &> /dev/null 2>&1; then
    print_success "Docker Compose is available"
else
    print_warning "Docker Compose is not available (needed for services)"
fi

# Check 2: Project structure
print_header "Checking Project Structure"

REQUIRED_FILES=(
    "pyproject.toml"
    "main.py"
    "Makefile"
    "requirements.txt"
)

for file in "${REQUIRED_FILES[@]}"; do
    if [ -f "$file" ]; then
        print_success "Found: $file"
    else
        print_warning "Missing: $file"
    fi
done

REQUIRED_DIRS=(
    "models"
    "utils"
    "scripts"
    "public"
)

for dir in "${REQUIRED_DIRS[@]}"; do
    if [ -d "$dir" ]; then
        print_success "Found directory: $dir"
    else
        print_error "Missing directory: $dir"
    fi
done

# Check 3: Dependencies installation
print_header "Checking Dependencies Installation"

if command -v uv &> /dev/null; then
    print_info "Attempting to sync dependencies..."
    if uv sync --dry-run &> /dev/null; then
        print_success "Dependencies can be resolved"
    else
        print_error "Failed to resolve dependencies with uv sync"
    fi
fi

# Check 4: Python imports check
print_header "Checking Core Python Module Imports"

if command -v uv &> /dev/null; then
    uv run python << 'EOF'
import sys
import importlib.util

modules_to_check = [
    'chainlit',
    'pydantic',
    'dotenv',
]

failed_imports = []
for module_name in modules_to_check:
    try:
        __import__(module_name)
        print(f"✓ {module_name} is available", file=sys.stdout)
    except ImportError as e:
        print(f"✗ {module_name} is NOT installed: {e}", file=sys.stderr)
        failed_imports.append(module_name)

if failed_imports:
    print(f"\nNote: Missing modules ({', '.join(failed_imports)}) will be installed with 'uv sync'", file=sys.stderr)
EOF
else
    python3 << 'EOF'
import sys
import importlib.util

modules_to_check = [
    'chainlit',
    'pydantic',
    'dotenv',
]

failed_imports = []
for module_name in modules_to_check:
    try:
        __import__(module_name)
        print(f"✓ {module_name} is available", file=sys.stdout)
    except ImportError as e:
        print(f"✗ {module_name} is NOT installed: {e}", file=sys.stderr)
        failed_imports.append(module_name)

if failed_imports:
    print(f"\nNote: Missing modules ({', '.join(failed_imports)}) will be installed with 'uv sync'", file=sys.stderr)
EOF
fi

# Check 5: File syntax validation
print_header "Checking Python Files for Syntax Errors"

PYTHON_FILES_TO_CHECK=(
    "main.py"
    "agent.py"
    "app_config.py"
    "database.py"
    "graph.py"
)

for py_file in "${PYTHON_FILES_TO_CHECK[@]}"; do
    if [ -f "$py_file" ]; then
        if python3 -m py_compile "$py_file" 2>/dev/null; then
            print_success "Syntax OK: $py_file"
        else
            print_error "Syntax error in: $py_file"
        fi
    else
        print_warning "File not found: $py_file"
    fi
done

# Check 6: Configuration files
print_header "Checking Configuration Files"

if [ -f "pyproject.toml" ]; then
    python3 << 'EOF'
try:
    import tomllib
except ImportError:
    import tomli as tomllib

try:
    with open('pyproject.toml', 'rb') as f:
        config = tomllib.load(f)
    print("✓ pyproject.toml is valid TOML")
    if 'project' in config or 'tool' in config:
        print("✓ pyproject.toml has expected sections")
except Exception as e:
    print(f"✗ Error parsing pyproject.toml: {e}")
EOF
fi

# Check 7: Docker services check
print_header "Checking Docker Compose Configuration"

if [ -f "docker-compose.yml" ]; then
    if command -v docker-compose &> /dev/null; then
        if docker-compose config > /dev/null 2>&1; then
            print_success "docker-compose.yml is valid"
        else
            print_error "docker-compose.yml has validation errors"
        fi
    elif docker compose version &> /dev/null 2>&1; then
        if docker compose config > /dev/null 2>&1; then
            print_success "docker-compose.yml is valid"
        else
            print_error "docker-compose.yml has validation errors"
        fi
    else
        print_warning "Cannot validate docker-compose.yml (Docker Compose not installed)"
    fi
fi

# Check 8: Environment files
print_header "Checking Environment Configuration"

if [ -f ".env" ]; then
    print_success ".env file exists"
else
    print_warning ".env file not found (may be needed at runtime)"
fi

if [ -f ".env.example" ]; then
    print_success ".env.example file exists"
else
    print_info ".env.example file not found"
fi

# Summary
print_header "Build Check Summary"

TOTAL_CHECKS=$((CHECKS_PASSED + CHECKS_FAILED))

echo -e "${GREEN}Passed: $CHECKS_PASSED${NC}"
echo -e "${RED}Failed: $CHECKS_FAILED${NC}"

if [ $CHECKS_FAILED -eq 0 ]; then
    echo -e "\n${GREEN}✓ All checks passed! Project appears ready to build.${NC}"
    print_info "Next step: Run 'make setup' to install dependencies"
    print_info "Then run: 'make run' to start the application"
    exit 0
else
    echo -e "\n${RED}✗ Some checks failed. Please review the errors above.${NC}"
    exit 1
fi
