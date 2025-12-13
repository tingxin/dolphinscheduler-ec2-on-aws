#!/bin/bash

# DolphinScheduler EC2 Quick Start Script

set -e

echo "=========================================="
echo "DolphinScheduler EC2 Quick Start"
echo "=========================================="
echo ""

# Check Python version
echo "Checking Python version..."
python_version=$(python3 --version 2>&1 | awk '{print $2}')
echo "Python version: $python_version"

# Create virtual environment
if [ ! -d "venv" ]; then
    echo ""
    echo "Creating virtual environment..."
    python3 -m venv venv
    echo "✓ Virtual environment created"
fi

# Activate virtual environment
echo ""
echo "Activating virtual environment..."
source venv/bin/activate

# Install dependencies
echo ""
echo "Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt
echo "✓ Dependencies installed"

# Install CLI tool
echo ""
echo "Installing CLI tool..."
pip install -e .
echo "✓ CLI tool installed"

# Check if config exists
if [ ! -f "my-cluster-config.yaml" ]; then
    echo ""
    echo "Creating configuration file..."
    cp config.yaml my-cluster-config.yaml
    echo "✓ Configuration file created: my-cluster-config.yaml"
    echo ""
    echo "⚠️  Please edit my-cluster-config.yaml with your AWS settings before proceeding"
fi

# Check if .env exists
if [ ! -f ".env" ]; then
    echo ""
    echo "Creating .env file..."
    cp .env.example .env
    echo "✓ .env file created"
    echo ""
    echo "⚠️  Please edit .env with your settings"
fi

echo ""
echo "=========================================="
echo "✓ Setup Complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "  1. Edit my-cluster-config.yaml with your AWS settings"
echo "  2. Edit .env with your credentials"
echo "  3. Validate configuration:"
echo "     python cli.py validate --config my-cluster-config.yaml"
echo "  4. Create cluster:"
echo "     python cli.py create --config my-cluster-config.yaml"
echo ""
echo "For help:"
echo "  python cli.py --help"
echo ""
