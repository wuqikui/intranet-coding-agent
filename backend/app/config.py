"""全局配置 - 全部从 .env 读取，无硬编码路径。"""
from pathlib import Path
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # --- 服务端口 ---
    BACKEND_PORT: int = 8000
    INFERENCE_PORT: int = 8001

    # --- 数据库 ---
    DATABASE_URL: str = "sqlite:///./data/app.db"
    AUDIT_LOG_PATH: str = "./data/audit"
    AUDIT_HASH_SEED: str = "genesis-block-seed"

    # --- 模型推理 ---
    INFERENCE_BACKEND: str = "mock"  # vllm | llama_cpp | mock
    MODEL_NAME: str = "qwen2.5-coder-32b-instruct"
    MODEL_PATH: str = "/models/qwen2.5-coder-32b"
    QUANTIZATION: str = "q4"  # awq | gptq | fp8 | q4 | q5 | q8
    TENSOR_PARALLEL_SIZE: int = 1
    MAX_MODEL_LEN: int = 32768
    GPU_MEMORY_UTILIZATION: float = 0.90

    # --- 显存自适应 ---
    SINGLE_CARD_STRATEGY: str = "32b_q4"
    MULTI_CARD_STRATEGY: str = "70b_q4"
    KV_CACHE_RESERVE: float = 0.15
    MAX_CONCURRENT_REQUESTS: int = 0  # 0 = 自动

    # --- 知识库 ---
    KB_BUSINESS_PATH: str = "./data/kb/business"
    KB_CODE_PATH: str = "./data/kb/code"
    KB_BUILDOPS_PATH: str = "./data/kb/buildops"
    KB_DATAAPI_PATH: str = "./data/kb/dataapi"
    CHROMA_PERSIST_PATH: str = "./data/chroma"
    SYMBOL_GRAPH_PATH: str = "./data/symbols.db"
    EMBEDDING_MODEL_PATH: str = "/models/embeddings/bge-large-zh"
    EMBEDDING_DIM: int = 1024

    # --- 沙箱 ---
    SANDBOX_MODE: str = "process"  # docker | rootless | process
    SANDBOX_IMAGE: str = "coding-sandbox:latest"
    WORKSPACE_ROOT: str = "./data/workspace"
    MAX_FIX_ROUNDS: int = 5

    # --- 鉴权 ---
    JWT_SECRET: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 480
    AUTH_MODE: str = "local"  # local | ldap | ad
    LDAP_URL: str = ""
    LDAP_BASE_DN: str = ""
    LDAP_BIND_DN: str = ""
    LDAP_BIND_PASSWORD: str = ""
    LDAP_USER_FILTER: str = "(sAMAccountName={username})"

    # --- 出网维护模式 ---
    MAINTENANCE_MODE: str = "false"
    MODEL_REPO_PATH: str = "/models"
    PACKAGE_REPO_PATH: str = "/packages"

    # --- 日志 ---
    LOG_LEVEL: str = "INFO"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

    def ensure_dirs(self):
        """确保所有运行时目录存在。"""
        for p in [
            self.AUDIT_LOG_PATH,
            self.WORKSPACE_ROOT,
            self.CHROMA_PERSIST_PATH,
            self.KB_BUSINESS_PATH,
            self.KB_CODE_PATH,
            self.KB_BUILDOPS_PATH,
            self.KB_DATAAPI_PATH,
        ]:
            Path(p).mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
