import argparse
import yaml
import logging
import sys
import socket
import warnings
from fastapi import FastAPI, Response
import uvicorn
from prometheus_client.exposition import CONTENT_TYPE_LATEST, generate_latest
from collector import RedfishMetricsCollector

config = None

with open("config.yml", "r") as config_file:
    config = yaml.load(config_file.read(), Loader=yaml.FullLoader)

app = FastAPI()

@app.get("/")
async def root():
    return {"message": "Hello This is Redfish Exporter"}

@app.get("/{endpoint}")
async def metric_router(target: str, endpoint: str, code: str):
    try:
        logging.debug("Request From Client")    
        config_data = config[code]
        username = config_data["auth"]["username"]
        password = config_data["auth"]["password"]
        if not username or not password: raise
    except:
        return Response(content="Authentication credentials get failed",status_code=401)
        
    registry = RedfishMetricsCollector(
        module=endpoint,
        host=target,
        username=username,
        password=password,
        code=code,
    )
    
    content = generate_latest(registry).decode('utf-8')
    return Response(content=content, media_type=CONTENT_TYPE_LATEST)

def enable_logging(args):
    logger = logging.getLogger()
    if args.debug:
        logger.setLevel(logging.DEBUG)
        loggerlevel = 'debug'
    elif args.warning:
        logger.setLevel(logging.WARNING)
        loggerlevel = 'warning'
    elif args.error:
        logger.setLevel(logging.ERROR)
        loggerlevel = 'error'
    else:
        logger.setLevel(logging.INFO)
        loggerlevel = 'info'
    
    format_str = '%(asctime)-15s %(process)d %(levelname)s %(filename)s:%(lineno)d %(message)s'
    if args.logging:
        logging.basicConfig(filename=args.logging, format=format_str)
    else:
        logging.basicConfig(stream=sys.stdout, format=format_str)
    return loggerlevel

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("-p", "--port", help="Listening Port", 
                       type=int, required=False)
    parser.add_argument("-d", "--debug", help="Debugging mode", 
                       action="store_true", required=False)
    parser.add_argument("-w", "--warning", help="Warning level logging", 
                       action="store_true", required=False)
    parser.add_argument("-e", "--error", help="Error level logging", 
                       action="store_true", required=False)
    parser.add_argument("-l", "--logging", help="Log file path", 
                       required=False)
    args = parser.parse_args()
    
    warnings.filterwarnings("ignore")
    
    loggerlevel = enable_logging(args)
    
    ip = socket.gethostbyname(socket.gethostname())
    logging.info("Listening on IP: %s", ip)
    
    workers = 1
    #workers = int(cpu_count()*5 +1)
    
    port = args.port if args.port else config['listen_port']
    
    uvicorn.run(
        "redfish_exporter:app",
        host="0.0.0.0",
        port=port,
        workers=workers,
        log_level=loggerlevel
    )