from loguru import logger 
import sys 

logger.remove()

FORMAT = """<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"""


logger.add(sys.stderr, format=FORMAT)
