.PHONY: help install test clean validate create delete status

help:
	@echo "DolphinScheduler EC2 CLI - Available Commands"
	@echo ""
	@echo "Setup:"
	@echo "  make install          Install dependencies and CLI tool"
	@echo "  make test             Run tests"
	@echo ""
	@echo "Cluster Management:"
	@echo "  make validate         Validate configuration"
	@echo "  make create           Create cluster"
	@echo "  make status           Check cluster status"
	@echo "  make delete           Delete cluster"
	@echo ""
	@echo "Development:"
	@echo "  make clean            Clean temporary files"
	@echo "  make format           Format code"
	@echo ""

install:
	@echo "Installing dependencies..."
	pip install -r requirements.txt
	pip install -e .
	@echo "✓ Installation complete"

test:
	@echo "Running tests..."
	pytest tests/ -v
	@echo "✓ Tests complete"

clean:
	@echo "Cleaning temporary files..."
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	rm -rf build/ dist/ .pytest_cache/
	@echo "✓ Cleanup complete"

validate:
	@echo "Validating configuration..."
	python cli.py validate --config my-cluster-config.yaml

create:
	@echo "Creating cluster..."
	python cli.py create --config my-cluster-config.yaml

status:
	@echo "Checking cluster status..."
	python cli.py status --config my-cluster-config.yaml

delete:
	@echo "Deleting cluster..."
	python cli.py delete --config my-cluster-config.yaml

format:
	@echo "Formatting code..."
	black src/ tests/ cli.py
	@echo "✓ Code formatted"
