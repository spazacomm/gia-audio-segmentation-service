# Comprehensive Monitoring System

## Overview

The Broadcast Audio Segmentation Service includes a comprehensive monitoring system that tracks both system resources and processing status in real-time. This provides visibility into service health, performance metrics, and processing progress.

## Monitoring Components

### 🏥 **Service Health Monitoring**

#### Basic Health Check
```bash
GET /health
```
Returns basic service status and uptime information.

#### Detailed Health Check
```bash
GET /health/detailed
```
Comprehensive health assessment including:
- Overall service status
- Component status (Database, GCS, System)
- System resource usage
- Error rates and performance metrics

### 💻 **System Resource Monitoring**

#### System Metrics
```bash
GET /metrics/system
```
Provides real-time system information:
- **CPU Usage**: Current CPU utilization percentage
- **Memory Usage**: RAM usage with total and used amounts
- **Disk Usage**: Storage utilization with free space
- **Process Count**: Number of running processes
- **Boot Time**: System uptime information

#### Example Response
```json
{
  "timestamp": "2025-12-17T18:14:59",
  "system": {
    "cpu_percent": 45.2,
    "memory_percent": 68.5,
    "memory_used_gb": 8.2,
    "memory_total_gb": 12.0,
    "disk_usage_percent": 73.1,
    "disk_free_gb": 156.8,
    "disk_total_gb": 512.0,
    "process_count": 234,
    "boot_time": "2025-12-17T06:00:00"
  },
  "service": {
    "uptime_seconds": 43200,
    "uptime_hours": 12.0,
    "total_requests": 156,
    "error_count": 3,
    "active_jobs": 2,
    "job_status_counts": {
      "pending": 0,
      "processing": 2,
      "completed": 154,
      "failed": 0
    }
  }
}
```

### ⚡ **Processing Status Monitoring**

#### Job Metrics
```bash
GET /metrics/jobs
```
Real-time job processing statistics:
- Total active jobs
- Jobs by status (processing, completed, failed)
- Processing time statistics
- Database connectivity status

#### Processing Dashboard
```bash
GET /metrics/dashboard
```
Comprehensive overview combining:
- Service health metrics
- System resource usage
- Processing statistics
- Recent broadcast activity
- Database status

### 🔄 **Real-time Job Tracking**

#### List All Jobs
```bash
GET /jobs
```
Shows all current and recent processing jobs with status details.

#### Specific Job Status
```bash
GET /jobs/{job_id}
```
Detailed information for a specific processing job:
- Job progress percentage
- Files processed vs. total files
- Segments created count
- Current file being processed
- Start/end timestamps

### 📊 **Visual Dashboard**

#### Web Dashboard
```bash
GET /dashboard
```
Real-time web-based monitoring dashboard featuring:
- **Service Health Panel**: Overall status and key metrics
- **System Resources Panel**: CPU, memory, disk usage
- **Processing Status Panel**: Active jobs and processing rates
- **Component Status Panel**: Database, GCS, system health
- **Active Jobs Table**: Real-time job progress with progress bars
- **Auto-refresh**: Automatic updates every 5 seconds

## Health Status Levels

### 🟢 **Healthy**
- All components operational
- System resources within normal limits
- No active errors

### 🟡 **Degraded**
- Some components experiencing issues
- System resources approaching limits (CPU > 80%, Memory > 85%)
- Minor errors detected

### 🔴 **Critical**
- Critical component failures
- System resources critically low (Disk > 90%)
- Service functionality impacted

### ⚫ **Error**
- Service cannot function properly
- Database or GCS connectivity issues
- Multiple component failures

## Monitoring Thresholds

### System Resource Alerts
- **CPU Usage**: Warning > 80%, Critical > 95%
- **Memory Usage**: Warning > 85%, Critical > 95%
- **Disk Usage**: Warning > 85%, Critical > 90%
- **Error Rate**: Warning > 5%, Critical > 10%

### Processing Alerts
- **Job Queue**: Monitor backlog for processing delays
- **Processing Time**: Alert on unusually long processing times
- **Database Connectivity**: Immediate alert on connection failures

## API Usage Examples

### Python Monitoring Script
```python
import requests
import time

def monitor_service():
    """Monitor service health and processing status"""
    
    while True:
        try:
            # Get dashboard data
            response = requests.get('http://localhost:8000/metrics/dashboard')
            data = response.json()
            
            # Check service health
            overall_status = data['overview']['service_status']
            error_rate = data['overview']['error_rate_percent']
            
            print(f"Service Status: {overall_status}")
            print(f"Error Rate: {error_rate}%")
            print(f"Active Jobs: {data['overview']['active_jobs']}")
            
            # Check system resources
            system = data['system']
            print(f"CPU: {system['cpu_percent']}%")
            print(f"Memory: {system['memory_percent']}%")
            print(f"Disk: {system['disk_usage_percent']}%")
            
            # Check for alerts
            if error_rate > 5:
                print("⚠️  High error rate detected!")
            
            if system['cpu_percent'] > 80:
                print("⚠️  High CPU usage!")
                
            if system['memory_percent'] > 85:
                print("⚠️  High memory usage!")
            
            print("-" * 50)
            time.sleep(30)  # Check every 30 seconds
            
        except Exception as e:
            print(f"Monitoring error: {e}")
            time.sleep(10)

# Run monitoring
monitor_service()
```

### Real-time Job Monitoring
```python
def monitor_jobs():
    """Monitor specific job progress"""
    
    # Start a segmentation job
    response = requests.post('http://localhost:8000/segment', json={
        "source_id": "capital-fm-uuid",
        "gcs_prefix": "kenya/radio/capital-fm/2025-12-16/"
    })
    
    job_info = response.json()
    job_id = job_info['job_id']
    
    print(f"Job started: {job_id}")
    
    # Monitor job progress
    while True:
        response = requests.get(f'http://localhost:8000/jobs/{job_id}')
        job_data = response.json()
        
        status = job_data['status']
        progress = job_data['progress_percent']
        files_done = job_data['files_processed']
        files_total = job_data['files_found']
        segments = job_data['segments_created']
        
        print(f"Status: {status}")
        print(f"Progress: {progress}%")
        print(f"Files: {files_done}/{files_total}")
        print(f"Segments: {segments}")
        
        if status in ['completed', 'failed']:
            print(f"Job {status}!")
            break
        
        time.sleep(10)  # Check every 10 seconds

monitor_jobs()
```

### System Health Check
```python
def check_system_health():
    """Comprehensive system health check"""
    
    response = requests.get('http://localhost:8000/health/detailed')
    health = response.json()
    
    print(f"Overall Status: {health['status']}")
    
    # Check components
    for component, status in health['components'].items():
        component_status = status['status']
        print(f"{component}: {component_status}")
        
        if component_status == 'error':
            print(f"  Error: {status.get('error', 'Unknown error')}")
    
    # Check metrics
    metrics = health['metrics']
    print(f"Uptime: {metrics['uptime_hours']} hours")
    print(f"Total Requests: {metrics['total_requests']}")
    print(f"Error Count: {metrics['error_count']}")

check_system_health()
```

## Logging and Alerting

### Log Levels
- **INFO**: Normal operations and job progress
- **WARNING**: Resource usage approaching limits
- **ERROR**: Component failures and processing errors
- **CRITICAL**: Service-wide failures

### Alert Conditions
- System resources exceed thresholds
- Database connectivity issues
- High error rates (> 5%)
- Job processing failures
- Service health degradation

## Integration with External Systems

### Prometheus Metrics
The service can be extended to expose Prometheus-compatible metrics for integration with monitoring systems like Grafana.

### Webhook Notifications
Alerts can be sent to external systems via webhooks when critical conditions are detected.

### Log Aggregation
All monitoring data can be exported to log aggregation systems (ELK Stack, Splunk, etc.) for centralized monitoring.

## Dashboard Features

### Auto-refresh
- Automatically updates every 5 seconds
- Toggle on/off with refresh button
- Manual refresh available

### Responsive Design
- Mobile-friendly interface
- Adapts to different screen sizes
- Touch-optimized controls

### Real-time Updates
- Live progress bars for active jobs
- Dynamic status indicators
- Real-time metric updates

## Best Practices

### Regular Monitoring
- Monitor system resources hourly
- Check job success rates daily
- Review error logs weekly

### Threshold Tuning
- Adjust alert thresholds based on your environment
- Monitor baseline performance patterns
- Update thresholds as usage patterns change

### Proactive Maintenance
- Monitor disk space usage
- Track memory leaks over time
- Monitor database query performance

This comprehensive monitoring system ensures you have full visibility into your audio segmentation service's health, performance, and processing status.