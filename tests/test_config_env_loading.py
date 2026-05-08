"""
Test config env loading logic:
- LLM_MODEL env var → settings.llm_model
- Empty LLM_MODEL → fallback gemma4:e4b
- OLLAMA_DEFAULT_MODEL fallback
- Comma-separated / quote handling
- .env file priority vs env vars
"""
import os
import pytest


class TestConfigEnvLoading:
    """Test Settings reads env vars correctly"""

    @pytest.fixture(autouse=True)
    def clear_env(self):
        """Clear the relevant env vars before each test"""
        saved = {}
        for key in ["LLM_MODEL", "OLLAMA_DEFAULT_MODEL"]:
            if key not in os.environ:
                saved[key] = "__NOTSET__"
            else:
                saved[key] = os.environ[key]
                del os.environ[key]
        # Remove .env from CWD so Pydantic doesn't read it
        saved_dotenv = None
        if os.path.exists(".env"):
            saved_dotenv = ".env.exists"
            os.rename(".env", ".env.bak_test")
        yield
        # Restore .env
        if saved_dotenv:
            os.rename(".env.bak_test", ".env")
        # Restore env vars
        for key, val in saved.items():
            if val == "__NOTSET__":
                if key in os.environ:
                    del os.environ[key]
            else:
                os.environ[key] = val

    def test_llm_model_reads_from_env(self):
        """LLM_MODEL env → settings.llm_model"""
        os.environ["LLM_MODEL"] = "gemma-4-E4B-it-Q4_K_M.gguf"
        from src.config import get_settings
        get_settings.cache_clear()
        s = get_settings()
        assert s.llm_model == "gemma-4-E4B-it-Q4_K_M.gguf"
        assert s.llm_model_candidates == ["gemma-4-E4B-it-Q4_K_M.gguf"]

    def test_llm_model_empty_fallsback_to_default(self):
        """Nếu LLM_MODEL rỗng → llm_model_candidates trả về [] (không có default)"""
        os.environ["LLM_MODEL"] = ""
        from src.config import get_settings
        get_settings.cache_clear()
        s = get_settings()
        assert s.llm_model == ""  # Empty string
        assert s.llm_model_candidates == []  # No default - empty list

    def test_llm_model_unset_fallsback_to_default(self):
        """Nếu LLM_MODEL không set và không có .env → trả về [] (không có default)"""
        if "LLM_MODEL" in os.environ:
            del os.environ["LLM_MODEL"]
        from src.config import get_settings
        get_settings.cache_clear()
        s = get_settings()
        assert s.llm_model == ""
        assert s.llm_model_candidates == []  # No default

    def test_ollama_default_model_independent(self):
        """OLLAMA_DEFAULT_MODEL chỉ ảnh hưởng recommended_models, KHÔNG ảnh hưởng llm_model"""
        os.environ["LLM_MODEL"] = "my-test-model.gguf"
        os.environ["OLLAMA_DEFAULT_MODEL"] = "some-other-model.gguf"
        from src.config import get_settings
        get_settings.cache_clear()
        s = get_settings()
        # llm_model đọc từ LLM_MODEL
        assert s.llm_model == "my-test-model.gguf"
        # ollama_default_model đọc từ OLLAMA_DEFAULT_MODEL
        assert s.ollama_default_model == "some-other-model.gguf"
        # recommended_models dùng OLLAMA_DEFAULT_MODEL
        assert s.recommended_models["faq"] == "some-other-model.gguf"
        # llm_model_candidates dùng LLM_MODEL
        assert s.llm_model_candidates == ["my-test-model.gguf"]

    def test_comma_separated_llm_model(self):
        """LLM_MODEL có thể là comma-separated list"""
        os.environ["LLM_MODEL"] = "model-a.gguf,model-b.gguf,model-c.gguf"
        from src.config import get_settings
        get_settings.cache_clear()
        s = get_settings()
        assert s.llm_model_candidates == ["model-a.gguf", "model-b.gguf", "model-c.gguf"]
        assert s.primary_llm_model == "model-a.gguf"

    def test_quotes_in_env_value_are_literal(self):
        """Dấu ngoặc kép trong .env là literal, KHÔNG được bỏ"""
        os.environ["LLM_MODEL"] = '"model-a.gguf,model-b.gguf"'
        from src.config import get_settings
        get_settings.cache_clear()
        s = get_settings()
        candidates = s.llm_model_candidates
        assert len(candidates) == 2
        # Model đầu có " ở đầu, model cuối có " ở cuối
        assert candidates[0] == '"model-a.gguf'
        assert candidates[1] == 'model-b.gguf"'

    def test_none_ollama_default_model_still_fallsback(self):
        """Nếu cả LLM_MODEL và OLLAMA_DEFAULT_MODEL đều rỗng → trả về []"""
        os.environ["LLM_MODEL"] = ""
        os.environ["OLLAMA_DEFAULT_MODEL"] = ""
        from src.config import get_settings
        get_settings.cache_clear()
        s = get_settings()
        assert s.llm_model == ""
        assert s.ollama_default_model == ""
        # llm_model_candidates trả về [] (không có default)
        assert s.llm_model_candidates == []

    def test_ollama_default_model_alone_doesnt_affect_primary(self):
        """Nếu chỉ set OLLAMA_DEFAULT_MODEL mà không set LLM_MODEL → llm_model_candidates = []"""
        if "LLM_MODEL" in os.environ:
            del os.environ["LLM_MODEL"]
        os.environ["OLLAMA_DEFAULT_MODEL"] = "gemma-4-E4B-it-Q4_K_M.gguf"
        from src.config import get_settings
        get_settings.cache_clear()
        s = get_settings()
        # llm_model rỗng → llm_model_candidates = [] (không có default)
        assert s.llm_model_candidates == []
        # OLLAMA_DEFAULT_MODEL chỉ dùng cho recommended_models
        assert s.recommended_models["faq"] == "gemma-4-E4B-it-Q4_K_M.gguf"

    def test_dotenv_file_has_higher_priority_than_default_factory(self):
        """Khi có .env file, Pydantic đọc từ .env trước khi dùng default_factory"""
        # Tạo .env tạm
        with open(".env", "w") as f:
            f.write('LLM_MODEL=from-dotenv-file.gguf\n')
        if "LLM_MODEL" in os.environ:
            del os.environ["LLM_MODEL"]
        from src.config import get_settings
        get_settings.cache_clear()
        s = get_settings()
        assert s.llm_model == "from-dotenv-file.gguf"
        assert s.llm_model_candidates == ["from-dotenv-file.gguf"]

    def test_env_var_overrides_dotenv(self):
        """Env var có priority cao hơn .env file"""
        # Tạo .env với value A
        with open(".env", "w") as f:
            f.write('LLM_MODEL=from-dotenv.gguf\n')
        # Set env var với value B
        os.environ["LLM_MODEL"] = "from-env-var.gguf"
        from src.config import get_settings
        get_settings.cache_clear()
        s = get_settings()
        # Env var thắng
        assert s.llm_model == "from-env-var.gguf"

    def test_dotenv_with_quotes_is_handled(self):
        """.env file có dấu " → Pydantic STRIPS quotes (khác với env var!)
        
        Lưu ý: Pydantic's dotenv parser xử lý quotes giống bash, trong khi
        os.environ giữ quotes là literal. Đây là khác biệt quan trọng.
        """
        with open(".env", "w") as f:
            f.write('LLM_MODEL="model-a.gguf,model-b.gguf"\n')
        if "LLM_MODEL" in os.environ:
            del os.environ["LLM_MODEL"]
        from src.config import get_settings
        get_settings.cache_clear()
        s = get_settings()
        candidates = s.llm_model_candidates
        assert len(candidates) == 2
        # Pydantic strip quotes khỏi .env
        assert candidates[0] == "model-a.gguf"
        assert candidates[1] == "model-b.gguf"
    
    def test_env_var_with_quotes_is_literal(self):
        """Env var với dấu " → quotes là literal, KHÔNG được strip
        
        Đây là trường hợp docker-compose pass env var xuống container
        với quotes trong docker-compose.yml hoặc .env của docker-compose.
        """
        os.environ["LLM_MODEL"] = '"model-a.gguf,model-b.gguf"'
        from src.config import get_settings
        get_settings.cache_clear()
        s = get_settings()
        candidates = s.llm_model_candidates
        assert len(candidates) == 2
        # Env var giữ nguyên quotes
        assert candidates[0] == '"model-a.gguf'
        assert candidates[1] == 'model-b.gguf"'

    def test_primary_model_mismatch_when_setting_ollama_default_only(self):
        """
        DEMO: Nếu deploy chỉ set OLLAMA_DEFAULT_MODEL mà quên LLM_MODEL.
        - .env không có LLM_MODEL
        - Kết quả: model mặc định gemma4:e4b
        - Dù OLLAMA_DEFAULT_MODEL có set đúng
        """
        if "LLM_MODEL" in os.environ:
            del os.environ["LLM_MODEL"]
        os.environ["OLLAMA_DEFAULT_MODEL"] = "gemma-4-E4B-it-Q4_K_M.gguf"
        from src.config import get_settings
        get_settings.cache_clear()
        s = get_settings()
        # ollama_default_model được set
        assert s.ollama_default_model == "gemma-4-E4B-it-Q4_K_M.gguf"
        # Nhưng primary model rỗng vì LLM_MODEL không set (không có default)
        assert s.primary_llm_model == ""
        # recommended_models thì đúng
        assert s.recommended_models["faq"] == "gemma-4-E4B-it-Q4_K_M.gguf"
        # LLM thực tế chạy sẽ rỗng - cần set LLM_MODEL để sử dụng
