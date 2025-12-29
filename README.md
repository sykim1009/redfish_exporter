## Architecture

### Overview

```
┌─────────────┐          ┌──────────────────┐          ┌─────────────┐
│             │          │                  │          │             │
│  Prometheus │◄─────────│ Redfish Exporter │◄─────────│   Redfish   │
│             │  HTTP    │                  │  HTTPS   │   API       │
│             │  :9640   │                  │  :443    │   (BMC)     │
└─────────────┘          └──────────────────┘          └─────────────┘
                                 │
                                 │
                                 ▼
                         ┌──────────────┐
                         │  config.yml  │
                         │              │
                         │ - Auth       │
                         │ - Endpoints  │
                         │ - Metrics    │
                         └──────────────┘
```

### Component Architecture

#### 1. **redfish_exporter.py** - Main Application
- FastAPI-based HTTP server
- Handles incoming Prometheus scrape requests
- Routes requests to appropriate collectors
- Manages configuration loading
- Provides health check endpoint

#### 2. **collector.py** - Metrics Collection Engine
- `RedfishMetricsCollector` class handles all metric collection
- Connects to Redfish API endpoints
- Collects hardware health and status information
- Transforms Redfish data into Prometheus metrics
- Implements connection pooling and error handling

#### 3. **config.yml** - Configuration Management
- Authentication credentials
- Metric definitions and paths
- Endpoint configurations
- Server-specific settings

### Data Flow

1. **Request Phase**
   ```
   Prometheus → GET /{endpoint}?target={host}&code={profile}
   ```

2. **Authentication Phase**
   ```
   Exporter → Load credentials from config.yml
   Exporter → Login to Redfish API
   ```

3. **Collection Phase**
   ```
   Exporter → Query Redfish endpoints
   Exporter → Parse JSON responses
   Exporter → Map to Prometheus metrics
   ```

4. **Response Phase**
   ```
   Exporter → Format as Prometheus metrics
   Exporter → Return to Prometheus
   ```

## Features

### Supported Metrics

#### System Metrics
- System health status
- Power state
- Manufacturer information
- Model and serial numbers

#### Processor Metrics
- CPU health status
- Clock speeds (base, max, operating)
- Core and thread counts
- Processor type and architecture
- GPU processor information (for HGX systems)

#### Memory Metrics
- Memory health status
- Capacity (MiB)
- Memory type and speed
- Device location
- Manufacturer details

#### GPU-Specific Metrics (HGX Baseboard)
- GPU system health
- GPU processor status
- GPU memory information
- FPGA status

#### Additional Metrics
- Thermal sensors
- Fan speeds
- Power consumption
- Power supply status
- Storage controllers
- PCIe devices
- Network interfaces

### Status Mapping

The exporter maps Redfish status values to numeric codes for Prometheus:

```python
'ok': 0           # Healthy
'on': 1           # Powered on
'critical': 1     # Critical error
'warning': 2      # Warning state
'unknown': 5      # Unknown status
'absent': 6       # Component absent
'get_failed': 99  # API fetch failed
```

## Installation

### Prerequisites

- Python 3.8+
- Access to server BMC/IPMI interface
- Network connectivity to Redfish API (port 443)

### Dependencies

```bash
pip install redfish
pip install fastapi
pip install uvicorn
pip install prometheus-client
pip install pyyaml
```

Or install from requirements.txt:

```bash
pip install -r requirements.txt
```

### Setup

1. Clone the repository:
```bash
git clone https://github.com/yourusername/redfish-exporter.git
cd redfish-exporter
```

2. Configure your servers in `config.yml`:
```yaml
listen_port: 9640

haein_gpu:
  auth:
    username: "ADMIN"
    password: "YourPassword"
  suffix: "-ipmi"
```

3. Run the exporter:
```bash
python redfish_exporter.py
```

## Usage

### Command Line Options

```bash
python redfish_exporter.py [OPTIONS]

Options:
  -p, --port PORT       Listening port (default: from config.yml)
  -d, --debug          Enable debug logging
  -w, --warning        Set warning level logging
  -e, --error          Set error level logging
  -l, --logging PATH   Log file path (default: stdout)
```

### Examples

Basic usage:
```bash
python redfish_exporter.py
```

With custom port and debug logging:
```bash
python redfish_exporter.py -p 9641 -d
```

With log file:
```bash
python redfish_exporter.py -l /var/log/redfish_exporter.log
```

### API Endpoints

#### Health Check
```bash
GET /
Response: {"message": "Hello This is Redfish Exporter"}
```

#### Metrics Endpoint
```bash
GET /{endpoint}?target={hostname}&code={profile}

Parameters:
  - endpoint: Metric endpoint name (e.g., "metrics")
  - target: Target hostname (without -ipmi suffix)
  - code: Configuration profile name (e.g., "haein_gpu")

Example:
curl "http://localhost:9640/metrics?target=server01&code=haein_gpu"
```

### Prometheus Configuration

Add to your `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: 'redfish'
    scrape_interval: 60s
    scrape_timeout: 30s
    metrics_path: '/metrics'
    static_configs:
      - targets:
          - server01
          - server02
          - server03
    relabel_configs:
      - source_labels: [__address__]
        target_label: __param_target
      - source_labels: [__param_target]
        target_label: instance
      - target_label: __address__
        replacement: localhost:9640
      - target_label: __param_code
        replacement: haein_gpu
```

## Configuration Reference

### Configuration Structure

```yaml
listen_port: 9640          # Exporter listening port

profile_name:              # Configuration profile
  auth:
    username: "ADMIN"      # Redfish username
    password: "password"   # Redfish password
  suffix: "-ipmi"          # Hostname suffix for IPMI
  
  metrics:
    category_name:         # Metric category
      base_path: "/redfish/v1/..."  # API endpoint path
      iterate: "Members"   # Optional: array field to iterate
      
      metrics:
        values:            # Metric values to collect
          - name: "health_status"
            path: ["Status", "Health"]
            type: "gauge"  # Optional: default is status mapping
            
        labels:            # Labels to attach
          - name: "Model"
            path: ["Model"]
```

### Adding New Server Profiles

1. Create a new profile in `config.yml`:
```yaml
new_server:
  auth:
    username: "admin"
    password: "password"
  suffix: "-ipmi"
  metrics:
    system:
      base_path: "/redfish/v1/Systems/1"
      metrics:
        values:
          - name: "health_status"
            path: ["Status", "Health"]
```

2. Update collector.py if custom logic is needed:
```python
if self._code == 'new_server':
    # Add custom collection logic
    pass
```

## Monitoring Best Practices

### Scrape Intervals
- Standard servers: 60-120 seconds
- Critical systems: 30-60 seconds
- GPU systems: 60 seconds (balance between freshness and load)

### Timeouts
- Set Prometheus scrape timeout to 30 seconds
- Exporter timeout set to 30 seconds
- Allows time for multiple API calls

### Resource Usage
- Memory: ~50-100MB per worker
- CPU: Minimal (event-driven)
- Network: ~1-5KB per scrape

### High Availability
- Run multiple exporter instances
- Use Prometheus federation
- Implement retry logic in Prometheus

## Troubleshooting

### Connection Issues

**Problem**: Connection timeout to BMC
```
Solution: 
1. Verify network connectivity: ping {hostname}-ipmi
2. Check BMC is responding on port 443
3. Verify firewall rules
4. Check BMC is not overloaded
```

**Problem**: Authentication failed
```
Solution:
1. Verify credentials in config.yml
2. Check account is not locked
3. Verify account has sufficient privileges
4. Try logging in via web interface
```

### Metric Issues

**Problem**: Missing metrics
```
Solution:
1. Enable debug logging: python redfish_exporter.py -d
2. Check Redfish API responses
3. Verify endpoint paths in config.yml
4. Check if hardware component is present
```

**Problem**: Incorrect status values
```
Solution:
1. Review status mapping in collector.py
2. Check actual Redfish API response
3. Add custom mapping if needed
```

### Performance Issues

**Problem**: Slow scrapes
```
Solution:
1. Reduce number of metrics collected
2. Increase scrape interval
3. Check BMC performance
4. Consider caching responses
```

## Development

### Code Structure

```
redfish-exporter/
├── redfish_exporter.py    # FastAPI application
├── collector.py           # Metrics collection logic
├── config.yml            # Server configurations
├── requirements.txt      # Python dependencies
└── README.md            # Documentation
```

### Adding New Metrics

1. Define metric in config.yml:
```yaml
new_metric:
  base_path: "/redfish/v1/NewEndpoint"
  metrics:
    values:
      - name: "new_value"
        path: ["Path", "To", "Value"]
```

2. Add collection method in collector.py:
```python
def _collect_new_metric(self, data):
    """Collect new metric"""
    value = self._safe_get(data, 'Path', 'To', 'Value')
    labels = {
        'labeltype': 'new_metric',
        'value_label': value
    }
    self._metrics.add_sample(
        self._module,
        value=self._map_status(value),
        labels=labels
    )
```

### Testing

Manual testing:
```bash
# Test health endpoint
curl http://localhost:9640/

# Test metrics collection
curl "http://localhost:9640/metrics?target=testserver&code=haein_gpu"

# Test with debug logging
python redfish_exporter.py -d
```

## Security Considerations

1. **Credential Management**
   - Store config.yml securely
   - Use restrictive file permissions (600)
   - Consider using environment variables for passwords
   - Rotate credentials regularly

2. **Network Security**
   - Use HTTPS for Redfish connections (enforced)
   - Restrict exporter port access
   - Use VLANs for management network
   - Implement firewall rules

3. **Access Control**
   - Create dedicated BMC user for monitoring
   - Assign minimal required privileges
   - Monitor for unauthorized access
   - Audit BMC access logs

## Acknowledgments

This project was inspired by and references the [SAP Converged Cloud Redfish Exporter](https://github.com/sapcc/redfish-exporter). We appreciate their contribution to the open-source community.

## License

[Specify your license here]

## Contributing

Contributions are welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to the branch
5. Create a Pull Request

## Support

For issues and questions:
- Create an issue on GitHub
- Check existing issues for solutions
- Provide debug logs when reporting issues

---

**Note**: This exporter is designed for production use but always test in a non-production environment first. Monitor BMC load and adjust scrape intervals accordingly.
