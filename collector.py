import redfish
import os
import logging
import time
import subprocess
import socket
from prometheus_client.metrics_core import GaugeMetricFamily

class RedfishMetricsCollector(object):
    def __init__(self, module, host, username, password, code):
        self._module = module
        self._host = f"{host}-ipmi"
        self._username = username
        self._password = password
        self._code = code
        self._timeout = 30
        self._redfish_object = None
        
        # 상태 매핑 딕셔너리 - 소문자로 통일
        self._status_map = {
            'off': 0, 'on': 1, 'absent': 6, 'ok': 0,
            'operable': 0, 'enabled': 0, 'good': 0,
            'goodinuse': 0, 'critical': 1, 'degraded': 1,
            'error': 1, 'warning': 2, 'unknown': 5,
            'null': 5, 'none': 5, 'presentunused': 7,
            'get_failed': 99, 'emptydata': 100, 'mapping_fail': 500,
        }
        
        self._start_time = time.time()
        
    def _map_status(self, status):
        """상태 값을 매핑하는 헬퍼 메서드"""
        if not status or status == 'None':
            return 5  # unknown
        return self._status_map.get(str(status).lower(), 500)
    
    def _safe_get(self, data, *keys, default='None'):
        """중첩된 딕셔너리에서 안전하게 값을 가져오는 헬퍼 메서드"""
        result = data
        for key in keys:
            if isinstance(result, dict):
                result = result.get(key, default)
            else:
                return default
        return str(result).strip() if result else default
    
    def ping_check(self):
        """ping을 사용하지 않고 socket으로 빠르게 연결 확인"""
        try:
            logging.debug(f"Target {self._host}: Connection Check")
            # socket을 사용한 빠른 연결 확인 (timeout 3초)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            result = sock.connect_ex((self._host, 443))
            sock.close()
            
            if result == 0:
                self._metrics.add_sample(
                    self._module, value=1,
                    labels={'labeltype': 'ping_check', 'ping_check': 'OK'}
                )
                logging.debug(f"Target {self._host}: Connection Check OK")
                return True
            else:
                self._metrics.add_sample(
                    self._module, value=0,
                    labels={'labeltype': 'ping_check', 'ping_check': 'Fail'}
                )
                logging.warning(f"Target {self._host}: Connection check failed")
                return False
        except Exception as e:
            logging.warning(f"Target {self._host}: Connection check error: {e}")
            self._metrics.add_sample(
                self._module, value=0,
                labels={'labeltype': 'ping_check', 'ping_check': 'Fail'}
            )
            return False
    
    def redfish_login(self):
        """Redfish 로그인"""
        try:
            logging.debug(f"Target {self._host}: Get Redfish Object")
            self._redfish_object = redfish.redfish_client(
                base_url=f"https://{self._host}",
                username=self._username,
                password=self._password,
                timeout=self._timeout,
                max_retry=2,
                default_prefix='/redfish/v1'
            )
            self._redfish_object.login(auth="session")
            self._metrics.add_sample(
                self._module, value=1,
                labels={'labeltype': 'redfish_login', 'redfish_login': 'OK'}
            )
            logging.debug(f"Target {self._host}: Get Redfish Object OK")
            return True
        except Exception as e:
            self._metrics.add_sample(
                self._module, value=0,
                labels={'labeltype': 'redfish_login', 'redfish_login': 'Failed'}
            )
            logging.error(f"Target {self._host}: Authorization Error: {e}")
            return False
    
    def _get_redfish_data(self, path):
        """Redfish 데이터를 가져오는 헬퍼 메서드"""
        try:
            response = self._redfish_object.get(path)
            if response.status == 200:
                return response.dict
            else:
                logging.warning(f"Target {self._host}: HTTP {response.status} for {path}")
                return None
        except Exception as e:
            logging.error(f"Target {self._host}: Error fetching {path}: {e}")
            return None

    def _collect_gpu_system_info(self, gpu_data):
        """시스템 기본 정보 수집"""
        status_health = self._safe_get(gpu_data, 'Status', 'Health')
        power_state = self._safe_get(gpu_data, 'PowerState')
        id = self._safe_get(gpu_data, 'Id')
        manufacturer = self._safe_get(gpu_data, 'Manufacturer')
        
        common_labels = {
            'Manufacturer': manufacturer,
            'Id': id,
        }
        
        # 시스템 헬스
        self._metrics.add_sample(
            self._module,
            value=self._map_status(status_health),
            labels={'labeltype': 'gpu_system_health', 'Status_Health': status_health, **common_labels}
        )
        
        # 전원 상태
        self._metrics.add_sample(
            self._module,
            value=self._map_status(power_state),
            labels={'labeltype': 'gpu_system_power', 'PowerState': power_state, **common_labels}
        )

    def _collect_gpu_processors(self, gpu_data):
        """프로세서 정보 수집"""
        processor_path = self._safe_get(gpu_data, 'Processors', '@odata.id')
        if processor_path == 'None':
            return
        
        processor_collection = self._get_redfish_data(processor_path)
        if not processor_collection:
            return
        
        members = processor_collection.get('Members', [])
        for processor in members:
            processor_data = self._get_redfish_data(processor.get('@odata.id'))
            if not processor_data:
                continue
            
            if self._safe_get(processor_data, 'Id') == "FPGA_0":
                labels = {
                    'labeltype': 'gpu_processor',
                    'Status_Health': self._safe_get(processor_data, 'Status', 'Health'),
                    'FirmwareVersion': self._safe_get(processor_data, 'FirmwareVersion'),
                    'Id': self._safe_get(processor_data, 'Id'),
                    'Manufacturer': self._safe_get(processor_data, 'Manufacturer'),
                    'Name': self._safe_get(processor_data, 'Name'),
                }
                self._metrics.add_sample(
                    self._module,
                    value=self._map_status(labels['Status_Health']),
                    labels=labels
                )

            else:
                labels = {
                    'labeltype': 'processor',
                    'Status_Health': self._safe_get(processor_data, 'Status', 'Health'),
                    'BaseSpeedMHz': self._safe_get(processor_data, 'BaseSpeedMHz'),
                    'FirmwareVersion': self._safe_get(processor_data, 'FirmwareVersion'),
                    'Id': self._safe_get(processor_data, 'Id'),
                    'Manufacturer': self._safe_get(processor_data, 'Manufacturer'),
                    'MaxSpeedMHz': self._safe_get(processor_data, 'MaxSpeedMHz'),
                    'Model': self._safe_get(processor_data, 'Model'),
                    'Name': self._safe_get(processor_data, 'Name'),
                    'OperatingSpeedMHz': self._safe_get(processor_data, 'OperatingSpeedMHz'),
                    'PartNumber': self._safe_get(processor_data, 'PartNumber'),
                    'ProcessorType': self._safe_get(processor_data, 'ProcessorType'),
                    'SerialNumber': self._safe_get(processor_data, 'SerialNumber'),
                    'TotalThreads': self._safe_get(processor_data, 'TotalThreads')
                }
            
                self._metrics.add_sample(
                    self._module,
                    value=self._map_status(labels['Status_Health']),
                    labels=labels
                )

    def _collect_gpu_memory(self, gpu_data):
        """메모리 정보 수집"""
        memory_path = self._safe_get(gpu_data, 'Memory', '@odata.id')
        if memory_path == 'None':
            return
        
        memory_collection = self._get_redfish_data(memory_path)
        if not memory_collection:
            return
        
        members = memory_collection.get('Members', [])
        for memory in members:
            memory_data = self._get_redfish_data(memory.get('@odata.id'))
            if not memory_data:
                continue
            
            labels = {
                'labeltype': 'gpu_memory',
                'Status_Health': self._safe_get(memory_data, 'Status', 'Health'),
                'CapacityMiB': self._safe_get(memory_data, 'CapacityMiB'),
                'Id': self._safe_get(memory_data, 'Id'),
                'MemoryDeviceType': self._safe_get(memory_data, 'MemoryDeviceType'),
                'MemoryType': self._safe_get(memory_data, 'MemoryType'),
                'Name': self._safe_get(memory_data, 'Name'),
                'OperatingSpeedMhz': self._safe_get(memory_data, 'OperatingSpeedMhz'),
            }
            
            self._metrics.add_sample(
                self._module,
                value=self._map_status(labels['Status_Health']),
                labels=labels
            )
    
    
    def _collect_system_info(self, system_data):
        """시스템 기본 정보 수집"""
        status_health = self._safe_get(system_data, 'Status', 'Health')
        power_state = self._safe_get(system_data, 'PowerState')
        manufacturer = self._safe_get(system_data, 'Manufacturer')
        model = self._safe_get(system_data, 'Model')
        id = self._safe_get(system_data, 'Id')
        part_number = self._safe_get(system_data, 'PartNumber')
        serial_number = self._safe_get(system_data, 'SerialNumber')
        
        common_labels = {
            'Id': id,
            'Manufacturer': manufacturer,
            'Model': model,
            'PartNumber': part_number,
            'SerialNumber': serial_number
        }
        
        # 시스템 헬스
        self._metrics.add_sample(
            self._module,
            value=self._map_status(status_health),
            labels={'labeltype': 'system_health', 'Status_Health': status_health, **common_labels}
        )
        
        # 전원 상태
        self._metrics.add_sample(
            self._module,
            value=self._map_status(power_state),
            labels={'labeltype': 'system_power', 'PowerState': power_state, **common_labels}
        )
    
    def _collect_processors(self, system_data):
        """프로세서 정보 수집"""
        processor_path = self._safe_get(system_data, 'Processors', '@odata.id')
        if processor_path == 'None':
            return
        
        processor_collection = self._get_redfish_data(processor_path)
        if not processor_collection:
            return
        
        members = processor_collection.get('Members', [])
        for processor in members:
            processor_data = self._get_redfish_data(processor.get('@odata.id'))
            if not processor_data:
                continue
            
            labels = {
                'labeltype': 'processor',
                'Status_Health': self._safe_get(processor_data, 'Status', 'Health'),
                'Id': self._safe_get(processor_data, 'Id'),
                'Manufacturer': self._safe_get(processor_data, 'Manufacturer'),
                'InstructionSet': self._safe_get(processor_data, 'InstructionSet'),
                'MaxSpeedMHz': self._safe_get(processor_data, 'MaxSpeedMHz'),
                'Model': self._safe_get(processor_data, 'Model'),
                'Name': self._safe_get(processor_data, 'Name'),
                'ProcessorArchitecture': self._safe_get(processor_data, 'ProcessorArchitecture'),
                'ProcessorType': self._safe_get(processor_data, 'ProcessorType'),
                'Socket': self._safe_get(processor_data, 'Socket'),
                'TotalCores': self._safe_get(processor_data, 'TotalCores'),
                'TotalThreads': self._safe_get(processor_data, 'TotalThreads')
            }
            
            self._metrics.add_sample(
                self._module,
                value=self._map_status(labels['Status_Health']),
                labels=labels
            )
    
    def _collect_memory(self, system_data):
        """메모리 정보 수집"""
        memory_path = self._safe_get(system_data, 'Memory', '@odata.id')
        if memory_path == 'None':
            return
        
        memory_collection = self._get_redfish_data(memory_path)
        if not memory_collection:
            return
        
        members = memory_collection.get('Members', [])
        for memory in members:
            memory_data = self._get_redfish_data(memory.get('@odata.id'))
            if not memory_data:
                continue
            
            labels = {
                'labeltype': 'memory',
                'Status_Health': self._safe_get(memory_data, 'Status', 'Health'),
                'CapacityMiB': self._safe_get(memory_data, 'CapacityMiB'),
                'DeviceLocator': self._safe_get(memory_data, 'DeviceLocator'),
                'Id': self._safe_get(memory_data, 'Id'),
                'Manufacturer': self._safe_get(memory_data, 'Manufacturer'),
                'Model': self._safe_get(memory_data, 'Model'),
                'MemoryDeviceType': self._safe_get(memory_data, 'MemoryDeviceType'),
                'MemoryType': self._safe_get(memory_data, 'MemoryType'),
                'Name': self._safe_get(memory_data, 'Name'),
                'OperatingSpeedMhz': self._safe_get(memory_data, 'OperatingSpeedMhz'),
                'PartNumber': self._safe_get(memory_data, 'PartNumber'),
                'SerialNumber': self._safe_get(memory_data, 'SerialNumber')
            }
            
            self._metrics.add_sample(
                self._module,
                value=self._map_status(labels['Status_Health']),
                labels=labels
            )
    
    def collect(self):
        """메트릭 수집 메인 메서드"""
        logging.getLogger('redfish').setLevel(logging.ERROR)
        self._metrics = GaugeMetricFamily(
            self._module,
            'Server Monitoring Data',
            labels={}
        )
        self._scrape_metrics = GaugeMetricFamily(
            "redfish_scrape_duration_seconds",
            "Server Monitoring Redfish Scrape duration in seconds",
            labels={}
        )
        
        try:
            if not self.ping_check():
                return
            
            if not self.redfish_login():
                return
            
            if self._code == 'haein_gpu':
                system_data = self._get_redfish_data("/redfish/v1/Systems/1")
                if system_data:
                    self._collect_system_info(system_data)
                    self._collect_processors(system_data)
                    self._collect_memory(system_data)
                
                gpu_data = self._get_redfish_data("/redfish/v1/Systems/HGX_Baseboard_0")
                if gpu_data:
                    self._collect_gpu_system_info(gpu_data)
                    self._collect_gpu_processors(gpu_data)
                    self._collect_gpu_memory(gpu_data)                
            
        except Exception as err:
            logging.error(f"Target {self._host}: An exception occurred: {err}")
        finally:
            if self._redfish_object:
                try:
                    self._redfish_object.logout()
                    logging.debug(f"Target {self._host}: Logged out successfully")
                except Exception as e:
                    logging.debug(f"Target {self._host}: Logout error: {e}")
            
            yield self._metrics
            
            duration = round(time.time() - self._start_time, 2)
            self._scrape_metrics.add_sample(
                'redfish_scrape_duration_seconds',
                value=duration,
                labels={}
            )
            yield self._scrape_metrics