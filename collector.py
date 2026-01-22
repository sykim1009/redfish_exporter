import redfish
import logging
import time
import socket
import yaml
import math
from ping3 import ping
from prometheus_client.metrics_core import GaugeMetricFamily

class RedfishMetricsCollector(object):
    def __init__(self, module, host, config):
        """
        Args:
            module: 쉼표로 구분된 모듈 문자열 (예: "processors,memory,thermal") 또는 "all"
            host: 서버 호스트명
            config: 서버 타입별 설정 딕셔너리
        """
        self._base_host = host
        self._config = config
        self._timeout = 30
        self._redfish_object = None
        
        # config에서 인증 정보 및 설정 가져오기
        self._username = config.get('auth', {}).get('username')
        self._password = config.get('auth', {}).get('password')
        self._suffix = config.get('suffix', '-ipmi')
        self._host = f"{host}{self._suffix}"
        
        # 전체 메트릭 설정
        all_metrics_config = config.get('metrics', {})
        
        # 모듈 파싱 및 필터링
        if module == 'all':
            # 모든 모듈 수집
            self._metrics_config = all_metrics_config
            self._selected_modules = list(all_metrics_config.keys())
        else:
            # 쉼표로 구분된 모듈 파싱
            requested_modules = [m.strip() for m in module.split(',')]
            self._selected_modules = requested_modules
            
            # 요청된 모듈만 필터링
            self._metrics_config = {
                k: v for k, v in all_metrics_config.items() 
                if k in requested_modules
            }
            
            # 존재하지 않는 모듈 경고
            available_modules = set(all_metrics_config.keys())
            invalid_modules = set(requested_modules) - available_modules
            if invalid_modules:
                logging.warning(f"Requested modules not found in config: {invalid_modules}")
                logging.warning(f"Available modules: {available_modules}")
        
        # 상태 매핑 딕셔너리
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
        return str(result).strip() if result and result != 'None' else default
    
    def _get_value_from_path(self, data, path):
        """path 리스트를 사용하여 중첩된 값 추출"""
        if not path:
            return 'None'
        return self._safe_get(data, *path)
    
    def connection_check(self):
        try:
            logging.debug(f"Target {self._host}: Ping Check")
            result = ping(self._host)
            
            if result is not False:
                self._metrics.add_sample(
                    'health', value=1,
                    labels={'module': 'connection', 'status': 'OK'}
                )
                logging.debug(f"Target {self._host}: Connection Check OK")
                return True
            else:
                self._metrics.add_sample(
                    'health', value=0,
                    labels={'module': 'connection', 'status': 'Fail'}
                )
                logging.warning(f"Target {self._host}: Connection check failed")
                return False
        except Exception as e:
            logging.warning(f"Target {self._host}: Connection check error: {e}")
            self._metrics.add_sample(
                'health', value=0,
                labels={'module': 'connection', 'status': 'Error'}
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
                max_retry=5,
                default_prefix='/redfish/v1'
            )
            self._redfish_object.login(auth="session")
            self._metrics.add_sample(
                'health', value=1,
                labels={'module': 'login', 'status': 'OK'}
            )
            logging.debug(f"Target {self._host}: Get Redfish Object OK")
            return True
        except Exception as e:
            self._metrics.add_sample(
                'health', value=0,
                labels={'module': 'login', 'status': 'Failed'}
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
    
    def _collect_metric_group(self, group_name, group_config):
        """설정 기반으로 메트릭 그룹 수집"""
        base_path = group_config.get('base_path')
        if not base_path:
            logging.warning(f"No base_path for {group_name}")
            return
        
        # 베이스 데이터 가져오기
        base_data = self._get_redfish_data(base_path)
        if not base_data:
            logging.warning(f"Failed to get data for {group_name} at {base_path}")
            return
        
        # iterate 설정이 있으면 배열 순회
        iterate_key = group_config.get('iterate')
        iterate_field = group_config.get('iterate_field')
        
        if iterate_key:
            # Members 배열 순회 (예: Processors)
            members = base_data.get(iterate_key, [])
            for member in members:
                member_path = member.get('@odata.id')
                if member_path:
                    member_data = self._get_redfish_data(member_path)
                    if member_data:
                        self._process_metric_data(group_name, group_config, member_data)
        elif iterate_field:
            # 특정 필드 배열 순회 (예: Temperatures)
            items = base_data.get(iterate_field, [])
            for item in items:
                self._process_metric_data(group_name, group_config, item)
        else:
            # 단일 객체 처리
            self._process_metric_data(group_name, group_config, base_data)
    
    def _process_metric_data(self, group_name, group_config, data):
        """단일 데이터 객체에서 메트릭 추출 및 등록"""
        metrics_config = group_config.get('metrics', {})
        value_configs = metrics_config.get('values', [])
        label_configs = metrics_config.get('labels', [])
        
        # 라벨 수집 (module 라벨 추가)
        labels = {'module': group_name}
        for label_config in label_configs:
            label_name = label_config.get('name')
            
            # value가 있으면 고정값 사용, 없으면 path에서 추출
            if 'value' in label_config:
                label_value = str(label_config['value'])
            else:
                label_path = label_config.get('path', [])
                label_value = self._get_value_from_path(data, label_path)
            
            labels[label_name] = label_value
        
        # 값 메트릭 수집
        for value_config in value_configs:
            value_name = value_config.get('name')
            value_path = value_config.get('path', [])
            value_type = value_config.get('type', 'status')  # 기본값은 'status'
            raw_value = self._get_value_from_path(data, value_path)
            
            # 메트릭 라벨 복사
            metric_labels = labels.copy()
            
            # type에 따라 값 처리
            if value_type == 'gauge':
                # gauge 타입: 숫자 값 그대로 사용
                try:
                    numeric_value = float(raw_value)
                except (ValueError, TypeError):
                    # 숫자가 아니면 NaN
                    numeric_value = math.nan
                    logging.debug(f"Non-numeric gauge value for {group_name}.{value_name}: {raw_value}, using NaN")
                
                # gauge도 라벨에 메트릭 이름 추가
                metric_labels[value_name] = str(raw_value)
            else:
                # status 타입: 상태 값을 숫자로 매핑
                numeric_value = self._map_status(raw_value)
                
                # 라벨에 원본 값 추가
                metric_labels[value_name] = raw_value
            
            # 메트릭 추가
            self._metrics.add_sample(
                'health',
                value=numeric_value,
                labels=metric_labels
            )
            
            logging.debug(f"Added metric: {group_name}.{value_name} = {numeric_value} ({raw_value})")
    
    def collect(self):
        logging.getLogger('redfish').setLevel(logging.ERROR)
        self._metrics = GaugeMetricFamily(
            'health',
            'Server Monitoring Data',
            labels={}
        )
        self._scrape_metrics = GaugeMetricFamily(
            "redfish_scrape_duration_seconds",
            "Server Monitoring Redfish Scrape duration in seconds",
            labels={}
        )
        
        try:
            # 수집할 모듈이 없으면 종료
            if not self._metrics_config:
                logging.warning(f"No valid modules to collect for {self._host}")
                self._metrics.add_sample(
                    'health', value=0,
                    labels={'module': 'error', 'status': 'No valid modules'}
                )
                yield self._metrics
                yield self._scrape_metrics
                return
            
            logging.info(f"Target {self._host}: Collecting modules: {self._selected_modules}")
            
            if not self.connection_check():
                yield self._metrics
                yield self._scrape_metrics
            else:           
                if not self.redfish_login():
                    yield self._metrics
                    yield self._scrape_metrics
                else:
                    for group_name, group_config in self._metrics_config.items():
                        try:
                            logging.debug(f"Collecting metrics for module: {group_name}")
                            self._collect_metric_group(group_name, group_config)
                        except Exception as e:
                            logging.error(f"Error collecting {group_name}: {e}")
                            self._metrics.add_sample(
                                'health', value=0,
                                labels={'module': group_name, 'status': 'collection_error'}
                            )
                            continue
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
                labels={'modules': ','.join(self._selected_modules)}
            )
            yield self._scrape_metrics
