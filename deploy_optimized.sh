#!/bin/bash

# Optimized deployment script for 2 vCPU, 4GB RAM systems
# This script configures and deploys the service for low-resource environments

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_header() {
    echo -e "${BLUE}============================================${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}============================================${NC}"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

check_system_resources() {
    print_header "Checking System Resources"
    
    # Check CPU
    CPU_CORES=$(nproc)
    print_success "CPU Cores: $CPU_CORES"
    
    if [ "$CPU_CORES" -lt 2 ]; then
        print_warning "Less than 2 CPU cores detected. Performance may be affected."
    fi
    
    # Check Memory
    MEMORY_GB=$(free -g | awk '/^Mem:/{print $2}')
    print_success "Memory: ${MEMORY_GB}GB"
    
    if [ "$MEMORY_GB" -lt 4 ]; then
        print_warning "Less than 4GB RAM detected. Consider using smaller audio files."
    fi
    
    # Check disk space
    DISK_GB=$(df -BG . | awk 'NR==2{print $4}' | sed 's/G//')
    print_success "Available disk space: ${DISK_GB}GB"
    
    if [ "$DISK_GB" -lt 10 ]; then
        print_warning "Less than 10GB disk space. Monitor temporary files closely."
    fi
}

install_optimized_dependencies() {
    print_header "Installing Optimized Dependencies"
    
    # Use optimized requirements
    if [ -f "requirements-optimized.txt" ]; then
        print_success "Installing optimized dependencies..."
        pip3 install -r requirements-optimized.txt --user --no-cache-dir
        print_success "Optimized dependencies installed"
    else
        print_error "requirements-optimized.txt not found"
        exit 1
    fi
    
    # Install additional optimizations
    pip3 install cachetools backoff --user --no-cache-dir
    print_success "Additional optimization libraries installed"
}

setup_optimized_environment() {
    print_header "Setting Up Optimized Environment"
    
    # Copy optimized environment file
    if [ -f ".env.optimized" ]; then
        cp .env.optimized .env
        print_success "Created optimized .env file"
        print_warning "Please edit .env file with your actual credentials"
    else
        print_error ".env.optimized not found"
        exit 1
    fi
    
    # Create optimized temp directory
    TEMP_DIR="/tmp/segmentation"
    if [ ! -d "$TEMP_DIR" ]; then
        mkdir -p "$TEMP_DIR"
        print_success "Created optimized temp directory: $TEMP_DIR"
    fi
    
    # Set up memory-limited temp filesystem (if possible)
    if mount | grep -q "$TEMP_DIR"; then
        print_success "Temp directory already mounted with limits"
    else
        print_warning "Consider mounting temp directory with memory limits:"
        echo "  sudo mount -t tmpfs -o size=512M tmpfs $TEMP_DIR"
    fi
}

optimize_system_settings() {
    print_header "Optimizing System Settings"
    
    # Set Python optimization environment variables
    export PYTHONUNBUFFERED=1
    export PYTHONDONTWRITEBYTECODE=1
    export PYTHONOPTIMIZE=1
    
    # Set service-specific optimizations
    export MAX_CONCURRENT_JOBS=1
    export MAX_MEMORY_MB=3072
    export AUDIO_CHUNK_SIZE_SECONDS=300
    export PROCESSING_TIMEOUT_SECONDS=1800
    
    print_success "System optimization variables set"
    
    # Suggest system-level optimizations
    print_warning "Consider these system-level optimizations:"
    echo "  1. Set swappiness: echo 'vm.swappiness=10' | sudo tee -a /etc/sysctl.conf"
    echo "  2. Increase file descriptors: echo '* soft nofile 65536' | sudo tee -a /etc/security/limits.conf"
    echo "  3. Optimize I/O scheduler: echo 'deadline' | sudo tee /sys/block/sda/queue/scheduler"
}

test_optimized_installation() {
    print_header "Testing Optimized Installation"
    
    print_warning "Testing Python imports..."
    python3 -c "
import gc
import psutil
import tempfile

# Test optimized imports
modules = ['fastapi', 'uvicorn', 'inaSpeechSegmenter', 'librosa', 'soundfile', 'psutil']
for module in modules:
    try:
        __import__(module)
        print(f'✅ {module}')
    except ImportError as e:
        print(f'❌ {module}: {e}')
        exit(1)

# Test memory monitoring
memory = psutil.virtual_memory()
print(f'✅ Memory monitoring: {memory.percent:.1f}% used')

# Test temp directory
temp_dir = tempfile.gettempdir()
print(f'✅ Temp directory: {temp_dir}')

print('✅ All optimized imports successful!')
" || {
        print_error "Import test failed"
        return 1
    }
    
    # Test optimized configuration
    if [ -f "main_optimized.py" ]; then
        print_success "Optimized main module found"
    else
        print_error "main_optimized.py not found"
        return 1
    fi
    
    return 0
}

start_optimized_service() {
    print_header "Starting Optimized Service"
    
    print_warning "Starting with optimized settings..."
    print_warning "Resource limits: 1 concurrent job, 3GB memory max"
    print_warning "Press Ctrl+C to stop"
    
    # Set environment variables for optimization
    export MAX_CONCURRENT_JOBS=1
    export MAX_MEMORY_MB=3072
    export LOG_LEVEL=WARNING
    
    # Start with optimized settings
    uvicorn main_optimized:app \
        --host 0.0.0.0 \
        --port 8000 \
        --workers 1 \
        --log-level warning \
        --access-log false \
        --loop asyncio
}

show_optimization_summary() {
    print_header "Optimization Summary"
    
    echo -e "${GREEN}Optimizations applied for 2 vCPU, 4GB RAM system:${NC}"
    echo ""
    echo "🔧 Resource Optimizations:"
    echo "  • Single concurrent job processing"
    echo "  • Memory usage capped at 3GB"
    echo "  • Garbage collection after each file"
    echo "  • Smaller batch sizes for database operations"
    echo "  • File size limits (100MB max)"
    echo ""
    echo "⚡ Performance Optimizations:"
    echo "  • CPU-only audio processing"
    echo "  • Simplified VAD engine"
    echo "  • Reduced logging overhead"
    echo "  • Sequential processing (no parallelism)"
    echo "  • Optimized temporary file management"
    echo ""
    echo "📊 Monitoring Optimizations:"
    echo "  • Lightweight metrics collection"
    echo "  • Reduced monitoring frequency"
    echo "  • Limited job tracking (10 jobs max)"
    echo "  • Simplified health checks"
    echo ""
    echo "🎯 Best Practices:"
    echo "  • Use audio files under 100MB"
    echo "  • Process files sequentially"
    echo "  • Monitor memory usage via /metrics/system"
    echo "  • Regular cleanup via /cleanup endpoint"
    echo "  • Use optimized temp directory"
    echo ""
    echo -e "${BLUE}Access Points:${NC}"
    echo "  • Service: http://localhost:8000"
    echo "  • Health: http://localhost:8000/health"
    echo "  • Metrics: http://localhost:8000/metrics/system"
    echo "  • Jobs: http://localhost:8000/jobs"
}

# Main execution
main() {
    print_header "Optimized Audio Segmentation Service Setup"
    
    # Parse arguments
    START_AFTER_SETUP=false
    DEPLOYMENT_TYPE="development"
    
    while [[ $# -gt 0 ]]; do
        case $1 in
            --start)
                START_AFTER_SETUP=true
                shift
                ;;
            --docker)
                DEPLOYMENT_TYPE="docker"
                shift
                ;;
            --help)
                echo "Usage: $0 [--start] [--docker] [--help]"
                echo ""
                echo "Options:"
                echo "  --start    Start the service after setup completes"
                echo "  --docker   Use Docker deployment"
                echo "  --help     Show this help message"
                exit 0
                ;;
            *)
                print_error "Unknown option: $1"
                echo "Use --help for usage information"
                exit 1
                ;;
        esac
    done
    
    # Run optimization steps
    check_system_resources
    install_optimized_dependencies
    setup_optimized_environment
    optimize_system_settings
    
    if test_optimized_installation; then
        show_optimization_summary
        
        if [ "$START_AFTER_SETUP" = true ]; then
            echo ""
            print_warning "Starting optimized service in 3 seconds..."
            sleep 3
            start_optimized_service
        elif [ "$DEPLOYMENT_TYPE" = "docker" ]; then
            echo ""
            print_warning "Building optimized Docker image..."
            docker build -f Dockerfile.optimized -t segmentation-service:optimized .
            print_success "Docker image built successfully"
            print_warning "Run with: docker run -p 8000:8000 segmentation-service:optimized"
        fi
    else
        print_error "Optimized installation test failed"
        print_warning "Check error messages above"
        exit 1
    fi
}

# Run main function
main "$@"