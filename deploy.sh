#!/bin/bash

# Broadcast Audio Segmentation Service Deployment Script
# This script helps deploy and manage the segmentation service

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
SERVICE_NAME="segmentation-service"
DOCKER_IMAGE="broadcast-segmentation"
CONTAINER_NAME="segmentation-service"
PORT=8000

# Functions
print_header() {
    echo -e "${BLUE}============================================${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}============================================${NC}"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

check_dependencies() {
    print_header "Checking Dependencies"
    
    # Check Python
    if command -v python3 &> /dev/null; then
        print_success "Python3 found: $(python3 --version)"
    else
        print_error "Python3 not found"
        exit 1
    fi
    
    # Check pip
    if command -v pip &> /dev/null; then
        print_success "pip found: $(pip --version)"
    else
        print_error "pip not found"
        exit 1
    fi
    
    # Check Docker (optional)
    if command -v docker &> /dev/null; then
        print_success "Docker found: $(docker --version)"
    else
        print_warning "Docker not found (optional for containerized deployment)"
    fi
}

setup_environment() {
    print_header "Setting Up Environment"
    
    # Create necessary directories
    mkdir -p logs temp
    
    # Copy environment file if it doesn't exist
    if [ ! -f .env ]; then
        cp .env.example .env
        print_success "Created .env file from template"
        print_warning "Please edit .env file with your credentials before starting the service"
    else
        print_success ".env file already exists"
    fi
}

install_dependencies() {
    print_header "Installing Python Dependencies"
    
    # Create virtual environment
    if [ ! -d "venv" ]; then
        python3 -m venv venv
        print_success "Created virtual environment"
    fi
    
    # Activate virtual environment
    source venv/bin/activate
    
    # Upgrade pip
    pip install --upgrade pip
    
    # Install dependencies
    pip install -r requirements.txt
    print_success "Dependencies installed"
}

run_tests() {
    print_header "Running Tests"
    
    # Check if service is running
    if ! curl -s http://localhost:${PORT}/health > /dev/null; then
        print_warning "Service is not running. Start the service first with: ./deploy.sh start"
        return
    fi
    
    # Run test script
    python3 test_service.py
}

start_service() {
    print_header "Starting Segmentation Service"
    
    # Check if service is already running
    if curl -s http://localhost:${PORT}/health > /dev/null; then
        print_warning "Service is already running on port ${PORT}"
        return
    fi
    
    # Check if .env exists
    if [ ! -f .env ]; then
        print_error ".env file not found. Run setup first."
        exit 1
    fi
    
    # Start with uvicorn
    source venv/bin/activate
    uvicorn main:app --host 0.0.0.0 --port ${PORT} --reload &
    
    SERVICE_PID=$!
    echo $SERVICE_PID > .service.pid
    
    print_success "Service started with PID ${SERVICE_PID}"
    print_success "Service available at http://localhost:${PORT}"
    print_success "API docs available at http://localhost:${PORT}/docs"
    
    # Wait for service to be ready
    echo "Waiting for service to be ready..."
    for i in {1..30}; do
        if curl -s http://localhost:${PORT}/health > /dev/null; then
            print_success "Service is ready!"
            return
        fi
        sleep 1
    done
    
    print_warning "Service may still be starting up"
}

stop_service() {
    print_header "Stopping Segmentation Service"
    
    if [ -f .service.pid ]; then
        PID=$(cat .service.pid)
        if kill -0 $PID 2>/dev/null; then
            kill $PID
            print_success "Service stopped (PID: ${PID})"
        else
            print_warning "Service was not running"
        fi
        rm .service.pid
    else
        print_warning "No PID file found"
    fi
}

restart_service() {
    print_header "Restarting Segmentation Service"
    stop_service
    sleep 2
    start_service
}

docker_build() {
    print_header "Building Docker Image"
    
    if ! command -v docker &> /dev/null; then
        print_error "Docker not found"
        exit 1
    fi
    
    docker build -t ${DOCKER_IMAGE} .
    print_success "Docker image built: ${DOCKER_IMAGE}"
}

docker_run() {
    print_header "Running Docker Container"
    
    # Check if .env exists
    if [ ! -f .env ]; then
        print_error ".env file not found. Run setup first."
        exit 1
    fi
    
    # Stop existing container
    docker stop ${CONTAINER_NAME} 2>/dev/null || true
    docker rm ${CONTAINER_NAME} 2>/dev/null || true
    
    # Run new container
    docker run -d \
        --name ${CONTAINER_NAME} \
        -p ${PORT}:8000 \
        --env-file .env \
        -v $(pwd)/temp:/tmp/segmentation \
        -v $(pwd)/logs:/app/logs \
        ${DOCKER_IMAGE}
    
    print_success "Docker container started: ${CONTAINER_NAME}"
    print_success "Service available at http://localhost:${PORT}"
}

docker_compose_up() {
    print_header "Starting with Docker Compose"
    
    if ! command -v docker-compose &> /dev/null; then
        print_error "docker-compose not found"
        exit 1
    fi
    
    docker-compose up -d
    print_success "Services started with docker-compose"
    print_success "Service available at http://localhost:${PORT}"
}

docker_compose_down() {
    print_header "Stopping Docker Compose Services"
    
    docker-compose down
    print_success "Docker Compose services stopped"
}

show_logs() {
    print_header "Showing Service Logs"
    
    if [ -f .service.pid ]; then
        print_warning "Service is running in foreground mode"
        print_warning "Logs are shown in the terminal"
        print_warning "Press Ctrl+C to stop"
        
        # Follow logs if using file-based logging
        if [ -f "logs/service.log" ]; then
            tail -f logs/service.log
        else
            print_warning "No log file found. Service may be logging to stdout."
        fi
    else
        if command -v docker &> /dev/null; then
            if docker ps | grep -q ${CONTAINER_NAME}; then
                docker logs -f ${CONTAINER_NAME}
            else
                print_error "No running service found"
            fi
        else
            print_error "No running service found"
        fi
    fi
}

show_status() {
    print_header "Service Status"
    
    if curl -s http://localhost:${PORT}/health > /dev/null; then
        print_success "Service is running on port ${PORT}"
        
        # Get health details
        response=$(curl -s http://localhost:${PORT}/health)
        echo "Health details: $response"
    else
        print_error "Service is not running on port ${PORT}"
    fi
}

show_help() {
    print_header "Available Commands"
    echo "  check          - Check dependencies"
    echo "  setup          - Set up environment and install dependencies"
    echo "  start          - Start the service"
    echo "  stop           - Stop the service"
    echo "  restart        - Restart the service"
    echo "  status         - Show service status"
    echo "  logs           - Show service logs"
    echo "  test           - Run test suite"
    echo "  docker-build   - Build Docker image"
    echo "  docker-run     - Run Docker container"
    echo "  compose-up     - Start with Docker Compose"
    echo "  compose-down   - Stop Docker Compose services"
    echo "  help           - Show this help"
}

# Main script
case "${1:-help}" in
    "check")
        check_dependencies
        ;;
    "setup")
        check_dependencies
        setup_environment
        install_dependencies
        ;;
    "start")
        start_service
        ;;
    "stop")
        stop_service
        ;;
    "restart")
        restart_service
        ;;
    "status")
        show_status
        ;;
    "logs")
        show_logs
        ;;
    "test")
        run_tests
        ;;
    "docker-build")
        docker_build
        ;;
    "docker-run")
        docker_run
        ;;
    "compose-up")
        docker_compose_up
        ;;
    "compose-down")
        docker_compose_down
        ;;
    "help"|*)
        show_help
        ;;
esac