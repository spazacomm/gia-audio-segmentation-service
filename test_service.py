#!/usr/bin/env python3
"""
Test script for Broadcast Audio Segmentation Service
"""

import requests
import json
import time
import sys
from datetime import datetime

# Configuration
SERVICE_URL = "http://localhost:8000"
TEST_SOURCE_ID = "test-source-uuid"
TEST_GCS_PREFIX = "kenya/radio/capital-fm/2025-12-16/"

def test_health_check():
    """Test the health check endpoint"""
    print("🔍 Testing health check...")
    try:
        response = requests.get(f"{SERVICE_URL}/health")
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Health check passed: {data['status']}")
            return True
        else:
            print(f"❌ Health check failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Health check error: {str(e)}")
        return False

def test_start_segmentation():
    """Test starting a segmentation job"""
    print("🚀 Starting segmentation test...")
    
    payload = {
        "source_id": TEST_SOURCE_ID,
        "gcs_prefix": TEST_GCS_PREFIX
    }
    
    try:
        response = requests.post(f"{SERVICE_URL}/segment", json=payload)
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Segmentation started: {data['message']}")
            print(f"   Multiple timelines will be created for audio files")
            return True
        else:
            print(f"❌ Segmentation failed: {response.status_code}")
            print(f"   Response: {response.text}")
            return False
    except Exception as e:
        print(f"❌ Segmentation error: {str(e)}")
        return False

def test_get_timeline(timeline_id):
    """Test getting timeline status"""
    print(f"📊 Checking timeline status for ID: {timeline_id}...")
    
    try:
        response = requests.get(f"{SERVICE_URL}/timeline/{timeline_id}")
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Timeline retrieved:")
            print(f"   Status: {data['status']}")
            print(f"   Broadcast Date: {data['broadcast_datetime']}")
            print(f"   Segments: {len(data['segments'])}")
            
            if data['segments']:
                print("   Sample segments:")
                for i, segment in enumerate(data['segments'][:3]):  # Show first 3
                    print(f"     {i+1}. {segment['label']}: {segment['start_time']:.1f}s - {segment['end_time']:.1f}s")
                if len(data['segments']) > 3:
                    print(f"     ... and {len(data['segments']) - 3} more segments")
            
            return data['status']
        else:
            print(f"❌ Timeline retrieval failed: {response.status_code}")
            print(f"   Response: {response.text}")
            return None
    except Exception as e:
        print(f"❌ Timeline error: {str(e)}")
        return None

def test_list_broadcasts():
    """Test listing broadcasts"""
    print("📋 Testing broadcast listing...")
    
    try:
        response = requests.get(f"{SERVICE_URL}/broadcasts?limit=10")
        if response.status_code == 200:
            data = response.json()
            broadcasts = data.get('broadcasts', [])
            print(f"✅ Retrieved {len(broadcasts)} broadcasts")
            
            if broadcasts:
                print("   Recent broadcasts:")
                for broadcast in broadcasts[:3]:  # Show first 3
                    print(f"     - {broadcast['broadcast_datetime']} ({broadcast['status']})")
            
            return True
        else:
            print(f"❌ Broadcast listing failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Broadcast listing error: {str(e)}")
        return False

def test_source_broadcasts():
    """Test getting broadcasts for a specific source"""
    print("📡 Testing source-specific broadcast listing...")
    
    try:
        response = requests.get(f"{SERVICE_URL}/broadcasts/{TEST_SOURCE_ID}")
        if response.status_code == 200:
            data = response.json()
            broadcasts = data.get('broadcasts', [])
            print(f"✅ Retrieved {len(broadcasts)} broadcasts for source")
            
            if broadcasts:
                print("   Recent broadcasts for source:")
                for broadcast in broadcasts[:3]:
                    print(f"     - {broadcast['broadcast_datetime']} ({broadcast['status']})")
            
            return True
        else:
            print(f"❌ Source broadcast listing failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Source broadcast error: {str(e)}")
        return False

def test_timeline_segments():
    """Test getting segments for a timeline"""
    print("🔗 Testing timeline segments retrieval...")
    
    try:
        # First get a timeline ID
        response = requests.get(f"{SERVICE_URL}/broadcasts?limit=1")
        if response.status_code != 200:
            print("❌ Could not get timeline for testing")
            return False
        
        data = response.json()
        broadcasts = data.get('broadcasts', [])
        
        if not broadcasts:
            print("⚠️  No broadcasts found to test segments")
            return True
        
        timeline_id = broadcasts[0]['id']
        
        # Get segments for this timeline
        response = requests.get(f"{SERVICE_URL}/segments/{timeline_id}")
        if response.status_code == 200:
            data = response.json()
            segments = data.get('segments', [])
            print(f"✅ Retrieved {len(segments)} segments for timeline {timeline_id}")
            
            if segments:
                print("   Sample segments:")
                for segment in segments[:3]:  # Show first 3
                    print(f"     - {segment['label']}: {segment['start_time']:.1f}s - {segment['end_time']:.1f}s")
            
            return True
        else:
            print(f"❌ Timeline segments retrieval failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Timeline segments error: {str(e)}")
        return False

def test_cleanup_endpoints():
    """Test cleanup-related endpoints"""
    print("🧹 Testing cleanup endpoints...")
    
    try:
        # Test listing temporary files
        response = requests.get(f"{SERVICE_URL}/temp-files")
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Temp files endpoint works: {data['count']} files found")
        else:
            print(f"❌ Temp files endpoint failed: {response.status_code}")
            return False
        
        # Test manual cleanup
        response = requests.post(f"{SERVICE_URL}/cleanup")
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Manual cleanup works: {data['message']}")
            return True
        else:
            print(f"❌ Manual cleanup failed: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Cleanup endpoints error: {str(e)}")
        return False

def test_monitoring_endpoints():
    """Test monitoring and metrics endpoints"""
    print("📊 Testing monitoring endpoints...")
    
    try:
        # Test system metrics
        response = requests.get(f"{SERVICE_URL}/metrics/system")
        if response.status_code == 200:
            data = response.json()
            print(f"✅ System metrics: CPU {data['system']['cpu_percent']}%, Memory {data['system']['memory_percent']}%")
        else:
            print(f"❌ System metrics failed: {response.status_code}")
            return False
        
        # Test job metrics
        response = requests.get(f"{SERVICE_URL}/metrics/jobs")
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Job metrics: {data['job_statistics']['total_jobs']} total jobs")
        else:
            print(f"❌ Job metrics failed: {response.status_code}")
            return False
        
        # Test dashboard data
        response = requests.get(f"{SERVICE_URL}/metrics/dashboard")
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Dashboard data: {data['overview']['active_jobs']} active jobs")
        else:
            print(f"❌ Dashboard data failed: {response.status_code}")
            return False
        
        # Test detailed health check
        response = requests.get(f"{SERVICE_URL}/health/detailed")
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Detailed health: {data['status']} status")
        else:
            print(f"❌ Detailed health failed: {response.status_code}")
            return False
        
        # Test jobs list
        response = requests.get(f"{SERVICE_URL}/jobs")
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Jobs list: {data['total']} total jobs")
        else:
            print(f"❌ Jobs list failed: {response.status_code}")
            return False
        
        return True
        
    except Exception as e:
        print(f"❌ Monitoring endpoints error: {str(e)}")
        return False



def run_full_test():
    """Run the complete test suite"""
    print("🎵 Starting Broadcast Audio Segmentation Service Test Suite")
    print("=" * 60)
    
    # Test 1: Health Check
    if not test_health_check():
        print("\n❌ Service is not healthy. Aborting tests.")
        return False
    
    print()
    
    # Test 2: Start Segmentation
    if not test_start_segmentation():
        print("\n❌ Could not start segmentation. Aborting tests.")
        return False
    
    print()
    
    # Test 3: List broadcasts (to see processing status)
    print("🔍 Checking broadcast list...")
    test_list_broadcasts()
    
    print()
    
    # Test 4: Test additional endpoints
    print("🧪 Testing additional endpoints...")
    test_source_broadcasts()
    test_timeline_segments()
    test_cleanup_endpoints()
    
    # Test 5: Test monitoring endpoints
    print("\n📊 Testing monitoring endpoints...")
    test_monitoring_endpoints()
    
    print("\n" + "=" * 60)
    print("✅ Test suite completed!")
    
    return True

def interactive_mode():
    """Interactive mode for manual testing"""
    print("🎛️  Interactive Mode - Manual API Testing")
    print("Commands: list, source, cleanup, temp, monitor, jobs, health, quit")
    
    while True:
        try:
            command = input("\nEnter command (list/source/cleanup/temp/quit): ").strip().lower()
            
            if command == 'quit':
                break
            elif command == 'list':
                test_list_broadcasts()
            elif command == 'source':
                source_id = input("Enter source ID: ").strip()
                if source_id:
                    try:
                        response = requests.get(f"{SERVICE_URL}/broadcasts/{source_id}")
                        if response.status_code == 200:
                            data = response.json()
                            broadcasts = data.get('broadcasts', [])
                            print(f"✅ Retrieved {len(broadcasts)} broadcasts for source {source_id}")
                        else:
                            print(f"❌ Failed to get broadcasts: {response.status_code}")
                    except Exception as e:
                        print(f"❌ Error: {str(e)}")
            elif command == 'cleanup':
                print("🧹 Running manual cleanup...")
                response = requests.post(f"{SERVICE_URL}/cleanup")
                if response.status_code == 200:
                    data = response.json()
                    print(f"✅ Cleanup completed: {data['message']}")
                else:
                    print(f"❌ Cleanup failed: {response.status_code}")
            elif command == 'temp':
                print("📁 Listing temporary files...")
                response = requests.get(f"{SERVICE_URL}/temp-files")
                if response.status_code == 200:
                    data = response.json()
                    print(f"✅ Found {data['count']} temporary files")
                    if data['temp_files']:
                        for file_info in data['temp_files'][:5]:  # Show first 5
                            print(f"   - {file_info['name']}: {file_info['size']} bytes")
                else:
                    print(f"❌ Failed to list temp files: {response.status_code}")
            
            elif command == 'monitor':
                print("📊 Getting monitoring dashboard data...")
                response = requests.get(f"{SERVICE_URL}/metrics/dashboard")
                if response.status_code == 200:
                    data = response.json()
                    overview = data['overview']
                    system = data['system']
                    print(f"✅ Service Status: {overview['service_status']}")
                    print(f"   Uptime: {overview['service_uptime_hours']} hours")
                    print(f"   Active Jobs: {overview['active_jobs']}")
                    print(f"   Error Rate: {overview['error_rate_percent']}%")
                    print(f"   CPU: {system['cpu_percent']}%")
                    print(f"   Memory: {system['memory_percent']}%")
                    print(f"   Disk: {system['disk_usage_percent']}%")
                else:
                    print(f"❌ Failed to get monitoring data: {response.status_code}")
            
            elif command == 'jobs':
                print("🔄 Listing processing jobs...")
                response = requests.get(f"{SERVICE_URL}/jobs")
                if response.status_code == 200:
                    data = response.json()
                    print(f"✅ Found {data['total']} jobs")
                    for job_id, job_data in data['jobs'].items():
                        status = job_data['status']
                        progress = job_data.get('progress_percent', 0)
                        print(f"   {job_id}: {status} ({progress}%)")
                else:
                    print(f"❌ Failed to list jobs: {response.status_code}")
            
            elif command == 'health':
                print("🏥 Getting detailed health check...")
                response = requests.get(f"{SERVICE_URL}/health/detailed")
                if response.status_code == 200:
                    data = response.json()
                    print(f"✅ Overall Status: {data['status']}")
                    print("   Components:")
                    for component, status in data['components'].items():
                        comp_status = status['status']
                        print(f"     {component}: {comp_status}")
                else:
                    print(f"❌ Failed to get health check: {response.status_code}")
            
            else:
                print("Unknown command. Available: list, source, cleanup, temp, monitor, jobs, health, quit")
                
        except KeyboardInterrupt:
            print("\n👋 Goodbye!")
            break
        except Exception as e:
            print(f"❌ Error: {str(e)}")

def main():
    """Main function"""
    if len(sys.argv) > 1 and sys.argv[1] == '--interactive':
        interactive_mode()
    else:
        # Default: run full test suite
        success = run_full_test()
        sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()