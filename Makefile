# Makefile for Broadcast Audio Segmentation Service

.PHONY: help setup start stop restart test validate docker-build docker-run compose-up compose-down clean logs status

# Default target
help:
	@echo "Broadcast Audio Segmentation Service - Makefile"
	@echo "=============================================="
	@echo ""
	@echo "Available commands:"
	@echo "  setup           - Set up environment and install dependencies"
	@echo "  validate        - Validate configuration and dependencies"
	@echo "  start           - Start the service"
	@echo "  stop            - Stop the service"
	@echo "  restart         - Restart the service"
	@echo "  status          - Check service status"
	@echo "  test            - Run test suite"
	@echo "  logs            - Show service logs"
	@echo "  docker-build    - Build Docker image"
	@echo "  docker-run      - Run Docker container"
	@echo "  compose-up      - Start with Docker Compose"
	@echo "  compose-down    - Stop Docker Compose services"
	@echo "  clean           - Clean up temporary files"
	@echo "  docs            - Open API documentation"
	@echo ""
	@echo "Examples:"
	@echo "  make setup      # First time setup"
	@echo "  make start      # Start development server"
	@echo "  make test       # Run tests"
	@echo "  make docker-run # Run with Docker"

# Setup environment
setup:
	@echo "Setting up environment..."
	@bash deploy.sh setup

# Validate configuration
validate:
	@echo "Validating configuration..."
	@python3 validate_config.py

# Start service
start:
	@echo "Starting segmentation service..."
	@bash deploy.sh start

# Stop service
stop:
	@echo "Stopping segmentation service..."
	@bash deploy.sh stop

# Restart service
restart:
	@echo "Restarting segmentation service..."
	@bash deploy.sh restart

# Check status
status:
	@echo "Checking service status..."
	@bash deploy.sh status

# Run tests
test:
	@echo "Running test suite..."
	@python3 test_service.py

# Show logs
logs:
	@echo "Showing service logs..."
	@bash deploy.sh logs

# Docker operations
docker-build:
	@echo "Building Docker image..."
	@bash deploy.sh docker-build

docker-run:
	@echo "Running Docker container..."
	@bash deploy.sh docker-run

compose-up:
	@echo "Starting with Docker Compose..."
	@bash deploy.sh compose-up

compose-down:
	@echo "Stopping Docker Compose services..."
	@bash deploy.sh compose-down

# Clean up
clean:
	@echo "Cleaning up temporary files..."
	@rm -rf temp/* logs/*
	@rm -f .service.pid
	@echo "Cleanup complete"

# Open API documentation
docs:
	@echo "Opening API documentation..."
	@python3 -c "import webbrowser; webbrowser.open('http://localhost:8000/docs')" || echo "Please start the service first: make start"

# Development shortcuts
dev: setup start
	@echo "Development environment ready!"

# Production deployment
prod: docker-build docker-run
	@echo "Production deployment ready!"

# Full test including validation
full-test: validate test
	@echo "Full test suite completed!"

# Quick start for new users
quickstart: setup validate start test
	@echo "Quick start completed! Service is running at http://localhost:8000"