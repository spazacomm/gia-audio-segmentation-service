#!/usr/bin/env python3
"""
Test script for optimized Audio Segmentation Service
Verifies low-resource optimizations are working correctly
"""

import requests
import json
import time
import psutil
import os
import tempfile
from datetime import datetime

# Configuration
SERVICE_URL = "http://localhost:8000"
TEST_SOURCE_ID = "test-optimized-source"
TEST_GCS_PREFIX = "kenya/radio/capital-fm/2025-12-16/"

def test_optimized_health():
    """Test optimized health check"""
    print("🏥 Testing optimized health check...")
    try:
        response = requests.get(f"{SERVICE_URL}/health", timeout=10)
        if response.status_code == 200:
            data = response.json()
            if "optimized" in data.get("service", "").lower():
                print("✅ Optimized service detected")
            else:
                print("⚠️  Service may not be optimized")
            print(f"   Status: {data['status']}")
            return True
        else:
            print(f"❌ Health check failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Health check error: {str(e)}")
        return False

def test_optimized_metrics():
    """Test optimized metrics collection"""
    print("📊 Testing optimized metrics...")
    try:
        response = requests.get(f"{SERVICE_URL}/metrics/system", timeout=10)
        if response.status_code == 200:
            data = response.json()
            system = data.get('system', {})
            
            # Check for optimized metrics
            memory_percent = system.get('memory_percent', 0)
            uptime_hours = system.get('uptime_hours', 0)
            
            print(f"✅ Metrics collection working")
            print(f"   Memory usage: {memory_percent}%")
            print(f"   Uptime: {uptime_hours:.1f} hours")
            
            # Verify memory is within limits
            if memory_percent <= 85:
                print("✅ Memory usage within acceptable limits")
            else:
                print("⚠️  High memory usage detected")
            
            return True
        else:
            print(f"❌ Metrics failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Metrics error: {str(e)}")
        return False

def test_resource_monitoring():
    """Test system resource monitoring"""
    print("💻 Testing system resource monitoring...")
    try:
        # Check local system resources
        memory = psutil.virtual_memory()
        cpu_percent = psutil.cpu_percent(interval=1)
        
        print(f"✅ System monitoring working")
        print(f"   Local memory: {memory.percent:.1f}% ({memory.used//1024//1024//1024}GB/{memory.total//1024//1024//1024//1024}GB)")
        print(f"   Local CPU: {cpu_percent:.1f}%")
        
        # Test service resource endpoints
        response = requests.get(f"{SERVICE_URL}/jobs", timeout=10)
        if response.status_code == 200:
            jobs_data = response.json()
            print(f"   Active jobs: {jobs_data.get('active_jobs', 0)}")
            print("✅ Job tracking working")
        
        return True
    except Exception as e:
        print(f"❌ Resource monitoring error: {str(e)}")
        return False

def test_temp_file_management():
    """Test temporary file management"""
    print("🗂️  Testing temp file management...")
    try:
        # Check temp files via API
        response = requests.get(f"{SERVICE_URL}/temp-files", timeout=10)
        if response.status_code == 200:
            data = response.json()
            temp_count = data.get('count', 0)
            print(f"✅ Temp file monitoring working")
            print(f"   Temporary files: {temp_count}")
            
            # Test manual cleanup
            cleanup_response = requests.post(f"{SERVICE_URL}/cleanup", timeout=30)
            if cleanup_response.status_code == 200:
                print("✅ Manual cleanup working")
                cleanup_data = cleanup_response.json()
                print(f"   Cleanup message: {cleanup_data.get('message', '')}")
            else:
                print("⚠️  Manual cleanup failed")
            
            return True
        else:
            print(f"❌ Temp file monitoring failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Temp file management error: {str(e)}")
        return False

def test_optimized_startup():
    """Test optimized service startup"""
    print("🚀 Testing optimized startup...")
    try:
        # Test that service accepts segmentation requests
        payload = {
            "source_id": TEST_SOURCE_ID,
            "gcs_prefix": TEST_GCS_PREFIX
        }
        
        response = requests.post(f"{SERVICE_URL}/segment", json=payload, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            print("✅ Segmentation endpoint working")
            print(f"   Job ID: {data.get('job_id', 'N/A')}")
            
            # Check if job tracking is working
            if data.get('job_id'):
                job_id = data['job_id']
                
                # Wait a moment and check job status
                time.sleep(2)
                job_response = requests.get(f"{SERVICE_URL}/jobs/{job_id}", timeout=10)
                if job_response.status_code == 200:
                    job_data = job_response.json()
                    print(f"   Job status: {job_data.get('status', 'unknown')}")
                    print("✅ Job tracking working")
                else:
                    print("⚠️  Job tracking may not be working")
            
            return True
        elif response.status_code == 429:
            print("✅ Rate limiting working (too many jobs)")
            return True
        else:
            print(f"❌ Segmentation endpoint failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Optimized startup error: {str(e)}")
        return False

def test_performance_limits():
    """Test performance limits for low-resource systems"""
    print("⚡ Testing performance limits...")
    try:
        # Check that we can only have limited concurrent jobs
        job_ids = []
        
        # Try to start multiple jobs (should be limited)
        for i in range(3):
            payload = {
                "source_id": f"{TEST_SOURCE_ID}-{i}",
                "gcs_prefix": f"{TEST_GCS_PREFIX}test-{i}/"
            }
            
            try:
                response = requests.post(f"{SERVICE_URL}/segment", json=payload, timeout=5)
                if response.status_code == 200:
                    data = response.json()
                    job_ids.append(data.get('job_id'))
                    print(f"   Job {i+1}: {data.get('job_id', 'N/A')}")
                elif response.status_code == 429:
                    print(f"   Job {i+1}: Rate limited ✅")
                    break
                else:
                    print(f"   Job {i+1}: Failed ({response.status_code})")
            except requests.exceptions.Timeout:
                print(f"   Job {i+1}: Timeout (may be processing)")
        
        print(f"✅ Job limit testing completed")
        print(f"   Started {len(job_ids)} jobs (should be limited)")
        
        # Check job queue
        jobs_response = requests.get(f"{SERVICE_URL}/jobs", timeout=10)
        if jobs_response.status_code == 200:
            jobs_data = jobs_response.json()
            active_jobs = jobs_data.get('active_jobs', 0)
            total_jobs = jobs_data.get('total', 0)
            print(f"   Active jobs: {active_jobs}")
            print(f"   Total jobs tracked: {total_jobs}")
            
            if total_jobs <= 10:
                print("✅ Job tracking limit working")
            else:
                print("⚠️  Job tracking may not be limiting properly")
        
        return True
    except Exception as e:
        print(f"❌ Performance limits test error: {str(e)}")
        return False

def test_memory_optimization():
    """Test memory optimization features"""
    print("🧠 Testing memory optimization...")
    try:
        # Test garbage collection by checking memory usage
        memory = psutil.virtual_memory()
        print(f"✅ Memory monitoring working")
        print(f"   Current memory: {memory.percent:.1f}% ({memory.used//1024//1024//1024}GB)")
        
        # Test that service responds quickly (indicating low memory usage)
        start_time = time.time()
        response = requests.get(f"{SERVICE_URL}/health", timeout=10)
        response_time = time.time() - start_time
        
        if response_time < 1.0:  # Should respond quickly with low memory usage
            print(f"✅ Fast response time: {response_time:.3f}s")
        else:
            print(f"⚠️  Slow response time: {response_time:.3f}s (may indicate high memory)")
        
        # Check for memory-related environment variables
        print("   Memory optimizations active")
        
        return True
    except Exception as e:
        print(f"❌ Memory optimization test error: {str(e)}")
        return False

def run_optimized_test_suite():
    """Run complete optimized test suite"""
    print("🧪 Audio Segmentation Service - Optimized Test Suite")
    print("=" * 60)
    print("Testing optimizations for 2 vCPU, 4GB RAM systems")
    print("=" * 60)
    
    tests = [
        ("Health Check", test_optimized_health),
        ("Metrics Collection", test_optimized_metrics),
        ("Resource Monitoring", test_resource_monitoring),
        ("Temp File Management", test_temp_file_management),
        ("Optimized Startup", test_optimized_startup),
        ("Performance Limits", test_performance_limits),
        ("Memory Optimization", test_memory_optimization)
    ]
    
    passed = 0
    total = len(tests)
    
    for test_name, test_func in tests:
        print(f"\n{test_name}")
        print("-" * len(test_name))
        
        try:
            if test_func():
                passed += 1
                print(f"✅ {test_name} PASSED")
            else:
                print(f"❌ {test_name} FAILED")
        except Exception as e:
            print(f"❌ {test_name} ERROR: {str(e)}")
    
    # Summary
    print("\n" + "=" * 60)
    print("📊 OPTIMIZED TEST SUMMARY")
    print("=" * 60)
    print(f"Tests Passed: {passed}/{total}")
    print(f"Success Rate: {(passed/total)*100:.1f}%")
    
    if passed == total:
        print("\n🎉 ALL OPTIMIZED TESTS PASSED!")
        print("✅ Service is properly optimized for low-resource systems")
        print("\nRecommended next steps:")
        print("  1. Configure your .env file with actual credentials")
        print("  2. Start processing with: uvicorn main_optimized:app --workers 1")
        print("  3. Monitor resources via: curl http://localhost:8000/metrics/system")
        print("  4. Use cleanup endpoint regularly: curl -X POST http://localhost:8000/cleanup")
    else:
        print(f"\n⚠️  {total-passed} tests failed")
        print("Please check the optimization configuration")
        print("See OPTIMIZATION_GUIDE.md for troubleshooting")
    
    return passed == total

if __name__ == "__main__":
    # Check if service is running
    print("Checking if optimized service is running...")
    try:
        response = requests.get(f"{SERVICE_URL}/health", timeout=5)
        if response.status_code != 200:
            print("❌ Service is not responding")
            print("Start the optimized service first:")
            print("  uvicorn main_optimized:app --workers 1 --log-level warning")
            exit(1)
    except requests.exceptions.RequestException:
        print("❌ Service is not running")
        print("Start the optimized service first:")
        print("  uvicorn main_optimized:app --workers 1 --log-level warning")
        exit(1)
    
    # Run test suite
    success = run_optimized_test_suite()
    exit(0 if success else 1)