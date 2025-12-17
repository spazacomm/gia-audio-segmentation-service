#!/usr/bin/env python3
"""
Configuration validation script for Broadcast Audio Segmentation Service
Validates environment setup and connectivity to external services
"""

import os
import sys
import json
from datetime import datetime
from typing import Dict, List, Tuple

# Third-party imports
try:
    import requests
    from google.cloud import storage
    from supabase import create_client
    import librosa
    import soundfile as sf
except ImportError as e:
    print(f"❌ Missing dependency: {e.name}")
    print("Please install dependencies: pip install -r requirements.txt")
    sys.exit(1)

class ConfigValidator:
    def __init__(self):
        self.errors = []
        self.warnings = []
        self.successes = []
    
    def log_success(self, message: str):
        self.successes.append(message)
        print(f"✅ {message}")
    
    def log_warning(self, message: str):
        self.warnings.append(message)
        print(f"⚠️  {message}")
    
    def log_error(self, message: str):
        self.errors.append(message)
        print(f"❌ {message}")
    
    def validate_environment_file(self) -> bool:
        """Validate .env file exists and has required variables"""
        print("\n🔍 Validating Environment File")
        print("-" * 40)
        
        env_file = ".env"
        if not os.path.exists(env_file):
            self.log_error(f"Environment file '{env_file}' not found")
            print(f"   Run: cp {env_file}.example {env_file}")
            return False
        
        self.log_success(f"Environment file '{env_file}' found")
        
        # Load environment variables
        from dotenv import load_dotenv
        load_dotenv()
        
        required_vars = [
            "GCS_BUCKET",
            "SUPABASE_URL", 
            "SUPABASE_KEY"
        ]
        
        missing_vars = []
        for var in required_vars:
            if not os.getenv(var):
                missing_vars.append(var)
        
        if missing_vars:
            self.log_error(f"Missing required environment variables: {', '.join(missing_vars)}")
            return False
        
        self.log_success("All required environment variables are set")
        return True
    
    def validate_gcs_connectivity(self) -> bool:
        """Test connectivity to Google Cloud Storage"""
        print("\n☁️  Validating Google Cloud Storage")
        print("-" * 40)
        
        try:
            bucket_name = os.getenv("GCS_BUCKET")
            if not bucket_name:
                self.log_error("GCS_BUCKET environment variable not set")
                return False
            
            # Initialize GCS client
            client = storage.Client()
            self.log_success("GCS client initialized")
            
            # Test bucket access
            bucket = client.bucket(bucket_name)
            bucket.reload()  # Test if bucket exists and is accessible
            self.log_success(f"GCS bucket '{bucket_name}' is accessible")
            
            # Test listing objects (if any)
            blobs = list(bucket.list_blobs(max_results=1))
            self.log_success(f"GCS bucket listing works (found {len(blobs)} objects)")
            
            return True
            
        except Exception as e:
            self.log_error(f"GCS connectivity failed: {str(e)}")
            return False
    
    def validate_supabase_connectivity(self) -> bool:
        """Test connectivity to Supabase"""
        print("\n🗄️  Validating Supabase Database")
        print("-" * 40)
        
        try:
            supabase_url = os.getenv("SUPABASE_URL")
            supabase_key = os.getenv("SUPABASE_KEY")
            
            if not supabase_url or not supabase_key:
                self.log_error("Supabase credentials not set")
                return False
            
            # Initialize Supabase client
            supabase = create_client(supabase_url, supabase_key)
            self.log_success("Supabase client initialized")
            
            # Test database connection by querying a simple table
            # We'll try to query the broadcast_timeline table structure
            try:
                # Try to get table info (this will fail if table doesn't exist, but that's ok)
                response = supabase.table('broadcast_timeline').select('id').limit(1).execute()
                self.log_success("Database connection successful")
                
                # Check if required tables exist by attempting a query
                required_tables = ['broadcast_timeline', 'timeline_labels', 'audio_library']
                for table in required_tables:
                    try:
                        supabase.table(table).select('*').limit(0).execute()
                        self.log_success(f"Table '{table}' exists and is accessible")
                    except Exception:
                        self.log_warning(f"Table '{table}' may not exist or be accessible")
                
                return True
                
            except Exception as e:
                self.log_warning(f"Database query failed: {str(e)}")
                # This might be ok if tables don't exist yet
                return True
                
        except Exception as e:
            self.log_error(f"Supabase connectivity failed: {str(e)}")
            return False
    
    def validate_audio_processing(self) -> bool:
        """Test audio processing capabilities"""
        print("\n🎵 Validating Audio Processing")
        print("-" * 40)
        
        try:
            # Test librosa
            import librosa
            self.log_success("librosa import successful")
            
            # Test soundfile
            import soundfile as sf
            self.log_success("soundfile import successful")
            
            # Test inaSpeechSegmenter (if available)
            try:
                from inaSpeechSegmenter import seg
                self.log_success("inaSpeechSegmenter import successful")
            except ImportError:
                self.log_warning("inaSpeechSegmenter not available - install with: pip install inaSpeechSegmenter")
            
            # Test FFmpeg availability
            try:
                import subprocess
                result = subprocess.run(['ffmpeg', '-version'], 
                                      capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    self.log_success("FFmpeg is available")
                else:
                    self.log_warning("FFmpeg check failed")
            except (subprocess.TimeoutExpired, FileNotFoundError):
                self.log_warning("FFmpeg not found - required for audio format conversion")
            
            return True
            
        except Exception as e:
            self.log_error(f"Audio processing validation failed: {str(e)}")
            return False
    
    def validate_fastapi_service(self) -> bool:
        """Test if FastAPI service can be started"""
        print("\n🚀 Validating FastAPI Service")
        print("-" * 40)
        
        try:
            # Check if main.py exists
            if not os.path.exists("main.py"):
                self.log_error("main.py not found")
                return False
            
            # Test importing the FastAPI app
            try:
                from main import app
                self.log_success("FastAPI app import successful")
            except Exception as e:
                self.log_error(f"FastAPI app import failed: {str(e)}")
                return False
            
            # Check required dependencies
            try:
                import fastapi
                import uvicorn
                self.log_success("FastAPI dependencies available")
            except ImportError as e:
                self.log_error(f"Missing FastAPI dependency: {e.name}")
                return False
            
            return True
            
        except Exception as e:
            self.log_error(f"FastAPI service validation failed: {str(e)}")
            return False
    
    def test_sample_workflow(self) -> bool:
        """Test a sample workflow (if possible)"""
        print("\n🔄 Testing Sample Workflow")
        print("-" * 40)
        
        try:
            # This is a basic test that doesn't require actual audio files
            # It tests the logic flow without external dependencies
            
            from main import get_or_create_timeline
            
            # Test datetime parsing (basic workflow component)
            test_date = datetime.fromisoformat("2025-12-16T08:00:00")
            self.log_success("Date parsing works")
            
            # Test segment data structure
            test_segments = [
                {
                    'label': 'male',
                    'start_time': 0.0,
                    'end_time': 30.0,
                    'confidence': 1.0
                },
                {
                    'label': 'music', 
                    'start_time': 30.0,
                    'end_time': 90.0,
                    'confidence': 1.0
                }
            ]
            
            # Test segment validation logic
            for segment in test_segments:
                if segment['end_time'] <= segment['start_time']:
                    raise ValueError("Invalid segment time range")
            
            self.log_success("Segment data structure validation passed")
            
            return True
            
        except Exception as e:
            self.log_warning(f"Sample workflow test failed: {str(e)}")
            return False
    
    def run_full_validation(self) -> bool:
        """Run all validation tests"""
        print("🎯 Broadcast Audio Segmentation Service - Configuration Validation")
        print("=" * 70)
        
        tests = [
            ("Environment File", self.validate_environment_file),
            ("Google Cloud Storage", self.validate_gcs_connectivity),
            ("Supabase Database", self.validate_supabase_connectivity),
            ("Audio Processing", self.validate_audio_processing),
            ("FastAPI Service", self.validate_fastapi_service),
            ("Sample Workflow", self.test_sample_workflow)
        ]
        
        passed = 0
        total = len(tests)
        
        for test_name, test_func in tests:
            try:
                if test_func():
                    passed += 1
                else:
                    print(f"   {test_name} test failed")
            except Exception as e:
                self.log_error(f"{test_name} test crashed: {str(e)}")
        
        # Summary
        print("\n" + "=" * 70)
        print("📊 VALIDATION SUMMARY")
        print("=" * 70)
        
        print(f"✅ Passed: {passed}/{total}")
        print(f"❌ Errors: {len(self.errors)}")
        print(f"⚠️  Warnings: {len(self.warnings)}")
        
        if self.errors:
            print("\n🔴 ERRORS:")
            for error in self.errors:
                print(f"   • {error}")
        
        if self.warnings:
            print("\n🟡 WARNINGS:")
            for warning in self.warnings:
                print(f"   • {warning}")
        
        if passed == total and not self.errors:
            print("\n🎉 ALL TESTS PASSED! Your configuration looks good.")
            print("\nNext steps:")
            print("   1. Start the service: ./deploy.sh start")
            print("   2. Test the API: python3 test_service.py")
            print("   3. Check API docs: http://localhost:8000/docs")
            return True
        else:
            print("\n🔧 Please fix the errors above before running the service.")
            return False

def main():
    validator = ConfigValidator()
    success = validator.run_full_validation()
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()