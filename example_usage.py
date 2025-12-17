#!/usr/bin/env python3
"""
Example usage of the simplified Broadcast Audio Segmentation Service API
This demonstrates how to use the service with the new simplified design
"""

import requests
import json
from datetime import datetime

# Configuration
SERVICE_URL = "http://localhost:8000"
SOURCE_ID = "capital-fm-uuid"  # Your actual source UUID
GCS_PREFIX = "kenya/radio/capital-fm/2025-12-16/"

def example_segmentation_workflow():
    """Complete workflow example"""
    
    print("🎵 Broadcast Audio Segmentation - Example Workflow")
    print("=" * 60)
    
    # Step 1: Start segmentation
    print("\n1. Starting segmentation for audio files...")
    payload = {
        "source_id": SOURCE_ID,
        "gcs_prefix": GCS_PREFIX
    }
    
    response = requests.post(f"{SERVICE_URL}/segment", json=payload)
    if response.status_code == 200:
        result = response.json()
        print(f"✅ Segmentation started: {result['message']}")
    else:
        print(f"❌ Failed to start segmentation: {response.status_code}")
        return
    
    # Step 2: Check service health
    print("\n2. Checking service health...")
    response = requests.get(f"{SERVICE_URL}/health")
    if response.status_code == 200:
        health = response.json()
        print(f"✅ Service is healthy: {health['status']}")
    
    # Step 3: List broadcasts to see processing status
    print("\n3. Checking broadcast processing status...")
    response = requests.get(f"{SERVICE_URL}/broadcasts?limit=20")
    if response.status_code == 200:
        data = response.json()
        broadcasts = data['broadcasts']
        print(f"✅ Found {len(broadcasts)} broadcasts")
        
        # Show status breakdown
        status_counts = {}
        for broadcast in broadcasts:
            status = broadcast['status']
            status_counts[status] = status_counts.get(status, 0) + 1
        
        print("   Status breakdown:")
        for status, count in status_counts.items():
            print(f"   - {status}: {count}")
    
    # Step 4: Get broadcasts for specific source
    print(f"\n4. Getting broadcasts for source {SOURCE_ID}...")
    response = requests.get(f"{SERVICE_URL}/broadcasts/{SOURCE_ID}?limit=10")
    if response.status_code == 200:
        data = response.json()
        source_broadcasts = data['broadcasts']
        print(f"✅ Found {len(source_broadcasts)} broadcasts for this source")
        
        # Show recent broadcasts
        if source_broadcasts:
            print("   Recent broadcasts:")
            for broadcast in source_broadcasts[:5]:
                dt = broadcast['broadcast_datetime']
                status = broadcast['status']
                print(f"   - {dt} ({status})")
    
    # Step 5: Get detailed segments for a completed broadcast
    print("\n5. Getting detailed segments for a completed broadcast...")
    
    # Find a completed broadcast
    completed_broadcast = None
    for broadcast in broadcasts:
        if broadcast['status'] == 'completed':
            completed_broadcast = broadcast
            break
    
    if completed_broadcast:
        timeline_id = completed_broadcast['id']
        print(f"   Getting segments for timeline {timeline_id}...")
        
        response = requests.get(f"{SERVICE_URL}/segments/{timeline_id}")
        if response.status_code == 200:
            data = response.json()
            segments = data['segments']
            print(f"✅ Found {len(segments)} segments")
            
            # Show segment breakdown
            label_counts = {}
            total_duration = 0
            
            for segment in segments:
                label = segment['label']
                duration = segment['end_time'] - segment['start_time']
                label_counts[label] = label_counts.get(label, 0) + 1
                total_duration += duration
            
            print(f"   Total duration: {total_duration:.1f} seconds")
            print("   Segment breakdown:")
            for label, count in label_counts.items():
                print(f"   - {label}: {count} segments")
            
            # Show sample segments
            print("   Sample segments:")
            for i, segment in enumerate(segments[:5]):
                label = segment['label']
                start = segment['start_time']
                end = segment['end_time']
                print(f"   {i+1}. {label}: {start:.1f}s - {end:.1f}s")
    else:
        print("   ⚠️  No completed broadcasts found yet")
    
    # Step 6: Get specific timeline details
    if completed_broadcast:
        print(f"\n6. Getting timeline details for broadcast {completed_broadcast['id']}...")
        response = requests.get(f"{SERVICE_URL}/timeline/{completed_broadcast['id']}")
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Timeline {data['id']}: {data['broadcast_datetime']}")
            print(f"   Status: {data['status']}")
            print(f"   Segments: {len(data['segments'])}")
    
    # Step 7: Test cleanup endpoints
    print("\n7. Testing cleanup functionality...")
    
    # List temporary files
    response = requests.get(f"{SERVICE_URL}/temp-files")
    if response.status_code == 200:
        data = response.json()
        print(f"✅ Found {data['count']} temporary files")
        if data['temp_files']:
            print("   Temporary files:")
            for file_info in data['temp_files'][:3]:  # Show first 3
                print(f"   - {file_info['name']}: {file_info['size']} bytes")
    
    # Manual cleanup
    print("   Running manual cleanup...")
    response = requests.post(f"{SERVICE_URL}/cleanup")
    if response.status_code == 200:
        data = response.json()
        print(f"✅ Cleanup completed: {data['message']}")
    else:
        print(f"❌ Cleanup failed: {response.status_code}")

def example_simple_api_calls():
    """Simple API call examples"""
    
    print("\n" + "=" * 60)
    print("📞 Simple API Call Examples")
    print("=" * 60)
    
    # Example 1: Start segmentation
    print("\nExample 1: Start segmentation")
    print("POST /segment")
    example_payload = {
        "source_id": "your-source-uuid",
        "gcs_prefix": "kenya/radio/capital-fm/2025-12-16/"
    }
    print(json.dumps(example_payload, indent=2))
    
    # Example 2: List broadcasts with pagination
    print("\nExample 2: List broadcasts with pagination")
    print("GET /broadcasts?source_id=uuid&limit=10&offset=0")
    
    # Example 3: Get source broadcasts
    print("\nExample 3: Get broadcasts for specific source")
    print("GET /broadcasts/{source_id}?limit=50")
    
    # Example 4: Get timeline segments
    print("\nExample 4: Get segments for timeline")
    print("GET /segments/{timeline_id}")
    
    # Example 5: Get specific timeline
    print("\nExample 5: Get specific timeline details")
    print("GET /timeline/{timeline_id}")
    
    # Example 6: Manual cleanup
    print("\nExample 6: Manual cleanup of temporary files")
    print("POST /cleanup")
    
    # Example 7: List temporary files
    print("\nExample 7: List temporary files for debugging")
    print("GET /temp-files")
    
    # Example 8: System monitoring
    print("\nExample 8: System monitoring")
    print("GET /metrics/system")
    
    # Example 9: Job monitoring
    print("\nExample 9: Job monitoring")
    print("GET /metrics/jobs")
    
    # Example 10: Dashboard
    print("\nExample 10: Real-time dashboard")
    print("GET /dashboard")

if __name__ == "__main__":
    print("Broadcast Audio Segmentation Service - API Examples")
    print("Make sure the service is running on http://localhost:8000")
    print()
    
    # Check if service is running
    try:
        response = requests.get(f"{SERVICE_URL}/health", timeout=5)
        if response.status_code != 200:
            print("❌ Service is not responding. Please start the service first.")
            print("Run: uvicorn main:app --reload --host 0.0.0.0 --port 8000")
            exit(1)
    except:
        print("❌ Service is not running. Please start the service first.")
        print("Run: uvicorn main:app --reload --host 0.0.0.0 --port 8000")
        exit(1)
    
    # Run examples
    example_segmentation_workflow()
    example_simple_api_calls()
    
    print("\n" + "=" * 60)
    print("✅ Examples completed!")
    print("For interactive testing, run: python3 test_service.py --interactive")