#!/bin/bash

# Quick setup script for Audio Segmentation Service
# This script installs dependencies and sets up the environment

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

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

check_python() {
    print_header "Checking Python Installation"
    
    if command -v python3 &> /dev/null; then
        PYTHON_VERSION=$(python3 --version)
        print_success "Python found: $PYTHON_VERSION"
        
        # Check Python version (need 3.8+)
        PYTHON_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
        PYTHON_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")
        
        if [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -ge 8 ]; then
            print_success "Python version is compatible (3.8+)"
        else
            print_error "Python 3.8+ required. Current version: $PYTHON_VERSION"
            exit 1
        fi
    else
        print_error "Python 3 not found. Please install Python 3.8 or higher."
        exit 1
    fi
}

install_system_dependencies() {
    print_header "Installing System Dependencies"
    
    # Detect OS
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        if command -v apt &> /dev/null; then
            print_success "Detected Ubuntu/Debian"
            print_warning "Installing system dependencies for audio processing..."
            sudo apt update
            sudo apt install -y libsndfile1 ffmpeg
            print_success "System dependencies installed"
        elif command -v yum &> /dev/null; then
            print_success "Detected CentOS/RHEL"
            print_warning "Installing system dependencies for audio processing..."
            sudo yum install -y libsndfile ffmpeg
            print_success "System dependencies installed"
        else
            print_warning "Unknown Linux distribution. Please install libsndfile1 and ffmpeg manually."
        fi
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        print_success "Detected macOS"
        if command -v brew &> /dev/null; then
            print_warning "Installing system dependencies for audio processing..."
            brew install libsndfile ffmpeg
            print_success "System dependencies installed"
        else
            print_warning "Homebrew not found. Please install manually: brew install libsndfile ffmpeg"
        fi
    else
        print_warning "Unknown OS. Please install libsndfile1 and ffmpeg manually."
    fi
}

install_python_dependencies() {
    print_header "Installing Python Dependencies"
    
    # Try main requirements.txt first
    if [ -f "requirements.txt" ]; then
        print_warning "Installing from requirements.txt..."
        if pip3 install -r requirements.txt --user; then
            print_success "Python dependencies installed successfully"
        else
            print_warning "Main requirements failed, trying pinned versions..."
            if [ -f "requirements-pinned.txt" ]; then
                pip3 install -r requirements-pinned.txt --user
                print_success "Python dependencies installed from pinned versions"
            else
                print_error "Failed to install Python dependencies"
                exit 1
            fi
        fi
    else
        print_error "requirements.txt not found"
        exit 1
    fi
}

setup_environment() {
    print_header "Setting Up Environment"
    
    if [ ! -f ".env" ]; then
        if [ -f ".env.example" ]; then
            cp .env.example .env
            print_success "Created .env file from template"
            print_warning "Please edit .env file with your credentials before starting the service"
        else
            print_warning ".env.example not found, creating basic .env file..."
            cat > .env << EOF
# Google Cloud Storage
GCS_BUCKET=your-bucket-name
GOOGLE_APPLICATION_CREDENTIALS=path/to/service-account-key.json

# Supabase
SUPABASE_URL=your-supabase-url
SUPABASE_KEY=your-supabase-key

# Service Configuration
FASTAPI_HOST=0.0.0.0
FASTAPI_PORT=8000
LOG_LEVEL=INFO
EOF
            print_success "Created basic .env file"
            print_warning "Please edit .env file with your actual credentials"
        fi
    else
        print_success ".env file already exists"
    fi
}

verify_installation() {
    print_header "Verifying Installation"
    
   
    print_warning # Test Python imports "Testing Python imports..."
    
    python3 -c "
import sys
required_modules = [
    'fastapi', 'uvicorn', 'pydantic', 'inaSpeechSegmenter',
    'librosa', 'soundfile', 'numpy', 'google.cloud.storage',
    'supabase', 'psutil', 'prometheus_client'
]

failed_modules = []
for module in required_modules:
    try:
        __import__(module)
        print(f'✅ {module}')
    except ImportError as e:
        print(f'❌ {module}: {e}')
        failed_modules.append(module)

if failed_modules:
    print(f'\\n❌ {len(failed_modules)} modules failed to import')
    sys.exit(1)
else:
    print('\\n✅ All modules imported successfully!')
" || {
        print_error "Some Python modules failed to import"
        return 1
    }
    
    # Test configuration validation if available
    if [ -f "validate_config.py" ]; then
        print_warning "Running configuration validation..."
        if python3 validate_config.py; then
            print_success "Configuration validation passed"
        else
            print_warning "Configuration validation failed - check your .env file"
        fi
    fi
    
    return 0
}

start_service() {
    print_header "Starting Service"
    
    print_warning "Starting Audio Segmentation Service..."
    print_warning "Press Ctrl+C to stop the service"
    
    if [ -f "main.py" ]; then
        python3 main.py
    else
        print_error "main.py not found"
        exit 1
    fi
}

show_next_steps() {
    print_header "Setup Complete!"
    
    echo -e "${GREEN}Your Audio Segmentation Service is ready!${NC}"
    echo ""
    echo "📋 Next steps:"
    echo "1. Edit .env file with your actual credentials:"
    echo "   - GCS_BUCKET: Your Google Cloud Storage bucket"
    echo "   - SUPABASE_URL: Your Supabase project URL"
    echo "   - SUPABASE_KEY: Your Supabase anonymous key"
    echo ""
    echo "2. Download Google Cloud service account key:"
    echo "   - Set GOOGLE_APPLICATION_CREDENTIALS path in .env"
    echo ""
    echo "3. Start the service:"
    echo "   python3 main.py"
    echo ""
    echo "4. Access the monitoring dashboard:"
    echo "   http://localhost:8000/dashboard"
    echo ""
    echo "5. Test the API:"
    echo "   python3 test_service.py"
    echo ""
    echo "📚 Documentation:"
    echo "   - README.md: Complete usage guide"
    echo "   - INSTALLATION_GUIDE.md: Troubleshooting"
    echo "   - MONITORING_GUIDE.md: Monitoring features"
}

# Main execution
main() {
    print_header "Audio Segmentation Service - Quick Setup"
    
    # Parse command line arguments
    START_AFTER_SETUP=false
    
    while [[ $# -gt 0 ]]; do
        case $1 in
            --start)
                START_AFTER_SETUP=true
                shift
                ;;
            --help)
                echo "Usage: $0 [--start] [--help]"
                echo ""
                echo "Options:"
                echo "  --start    Start the service after setup completes"
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
    
    # Run setup steps
    check_python
    install_system_dependencies
    install_python_dependencies
    setup_environment
    
    if verify_installation; then
        show_next_steps
        
        if [ "$START_AFTER_SETUP" = true ]; then
            echo ""
            print_warning "Starting service in 3 seconds..."
            sleep 3
            start_service
        fi
    else
        print_error "Installation verification failed"
        print_warning "Check the error messages above and see INSTALLATION_GUIDE.md"
        exit 1
    fi
}

# Run main function with all arguments
main "$@"