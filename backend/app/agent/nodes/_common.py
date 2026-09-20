"""节点共享工具：LLM 调用、JSON/骨架解析、语言与项目名推断、默认文件树。

设计原则：
- 所有 LLM 调用统一走 GatewayRouter（保证离线 mock 兜底）。
- 解析优先 JSON（真实 LLM），失败回退骨架解析（mock 模板），再失败回退启发式默认。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("agent.nodes")

# ---- LLM 调用 ----


def make_messages(system: str, user: str) -> List[Dict[str, str]]:
    """构造 OpenAI 兼容 messages。"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


async def call_llm(
    router: Any,
    user_id: str,
    role: str,
    system: str,
    user: str,
    max_tokens: int = 2048,
) -> str:
    """调用 GatewayRouter.chat（非流式），返回纯文本。"""
    msgs = make_messages(system, user)
    text = await router.chat(
        user_id=user_id,
        role=role,
        messages=msgs,
        max_tokens=max_tokens,
        stream=False,
    )
    return text or ""


# ---- 解析 ----


def parse_json_safely(text: str) -> Optional[Any]:
    """尽力从 LLM 文本中解析出 JSON。"""
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    # ```json ... ``` 代码块
    m = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # 首个 { 到末个 }
    s, e = text.find("{"), text.rfind("}")
    if s != -1 and e != -1 and e > s:
        try:
            return json.loads(text[s : e + 1])
        except Exception:
            pass
    # 首个 [ 到末个 ]
    s, e = text.find("["), text.rfind("]")
    if s != -1 and e != -1 and e > s:
        try:
            return json.loads(text[s : e + 1])
        except Exception:
            pass
    return None


# 骨架文件头：# --- <描述> (<路径>) ---
_HEADER_RE = re.compile(r"#\s*---\s*(.*?)\s*\(([^)]+)\)\s*---\s*\n")


def parse_skeleton(text: str) -> Dict[str, str]:
    """解析 mock 模板式的多文件骨架响应为 {path: content}。

    识别形如：
        # --- 描述 (src/foo.cpp) ---
        <内容>
        // 文件结束   或   # 文件结束
    """
    if not text:
        return {}
    headers = list(_HEADER_RE.finditer(text))
    if not headers:
        return {}
    files: Dict[str, str] = {}
    for i, m in enumerate(headers):
        path = m.group(2).strip()
        start = m.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        body = text[start:end]
        # 去掉末尾的"文件结束"标记行与空行
        lines = body.split("\n")
        while lines and (not lines[-1].strip() or "文件结束" in lines[-1]):
            lines.pop()
        files[path] = "\n".join(lines) + "\n"
    return files


# ---- 启发式推断 ----


def detect_language(query: str) -> str:
    """从用户需求推断主工程语言。"""
    q = (query or "").lower()
    if any(k in q for k in ("c++", "cpp", "cxx", "cmake", "gcc", "clang")):
        return "cpp"
    if "typescript" in q or ".ts" in q or " ts " in q:
        return "ts"
    if any(k in q for k in ("javascript", "node", "react", "vue", "npm")):
        return "javascript"
    if any(k in q for k in ("golang", "go语言", " go ", "go ")):
        return "go"
    if any(k in q for k in ("rust", "cargo")):
        return "rust"
    if any(k in q for k in ("java", "maven", "spring", "gradle")):
        return "java"
    if any(k in q for k in ("python", "django", "flask", "fastapi", "py")):
        return "python"
    # 中文场景默认 python
    return "python"


_CN_NUM_MAP = str.maketrans("０１２３４５６７８９", "0123456789")


def _slug(name: str, language: str = "python") -> str:
    """把项目名规范为合法的目录/包名（小写、下划线或短横）。"""
    name = (name or "").translate(_CN_NUM_MAP).strip()
    if not name:
        return "generated_project"
    # 中文/空格/标点 → 下划线
    s = re.sub(r"[^A-Za-z0-9_\-]+", "_", name).strip("_").lower()
    if not s:
        s = "generated_project"
    return s


def extract_project_name(query: str, language: str = "python") -> str:
    """从用户需求推断项目名。"""
    q = query or ""
    # 引号包裹
    m = re.search(r"[\"'“”]([^\"'“”]{2,40})[\"'“”]", q)
    if m:
        return _slug(m.group(1), language)
    # “叫 XXX” / “名为 XXX”
    m = re.search(r"(?:叫|名为|名字叫|项目名[是为])\s*([^\s，。,\.]{2,20})", q)
    if m:
        return _slug(m.group(1), language)
    # 语言后接的名词：一个 XXX (应用|程序|系统|...)
    m = re.search(r"(?:一个|个)?\s*([A-Za-z\u4e00-\u9fa5][A-Za-z0-9\u4e00-\u9fa5]{1,20})\s*(?:应用|程序|系统|项目|工具|服务|网站)", q)
    if m:
        return _slug(m.group(1), language)
    # calculator 这类英文词
    m = re.search(r"\b([a-zA-Z][a-zA-Z0-9_]{2,20})\b", q)
    if m:
        return _slug(m.group(1), language)
    return _slug("generated_" + language, language)


def default_file_tree(language: str, project_name: str) -> List[str]:
    """按语言返回默认骨架文件树。"""
    name = _slug(project_name, language)
    if language == "cpp":
        return [
            "src/main.cpp",
            "src/%s.cpp" % name,
            "include/%s.h" % name,
            "CMakeLists.txt",
            "README.md",
        ]
    if language in ("ts", "javascript"):
        ext = "ts" if language == "ts" else "js"
        return [
            "package.json",
            "src/index.%s" % ext,
            "README.md",
        ]
    if language == "go":
        return ["main.go", "go.mod", "README.md"]
    if language == "rust":
        return ["src/main.rs", "Cargo.toml", "README.md"]
    if language == "java":
        return ["src/main/java/App.java", "pom.xml", "README.md"]
    # python 默认
    return ["main.py", "requirements.txt", "README.md"]


def default_skeleton(language: str, project_name: str) -> Dict[str, str]:
    """按语言返回最小可运行骨架文件内容（mock 降级用）。"""
    name = _slug(project_name, language)
    if language == "cpp":
        return {
            "src/%s.cpp" % name: (
                "#include \"%s.h\"\n"
                "#include <stdexcept>\n\n"
                "double %s_add(double a, double b) { return a + b; }\n"
                "double %s_sub(double a, double b) { return a - b; }\n"
                "double %s_mul(double a, double b) { return a * b; }\n"
                "double %s_div(double a, double b) {\n"
                "    if (b == 0) throw std::runtime_error(\"div by zero\");\n"
                "    return a / b;\n"
                "}\n"
            )
            % tuple([name] * 5),
            "include/%s.h" % name: (
                "#pragma once\n"
                "double %s_add(double a, double b);\n"
                "double %s_sub(double a, double b);\n"
                "double %s_mul(double a, double b);\n"
                "double %s_div(double a, double b);\n"
            )
            % tuple([name] * 4),
            "src/main.cpp": (
                "#include <iostream>\n"
                "#include \"%s.h\"\n\n"
                "int main() {\n"
                "    std::cout << \"1+2=\" << %s_add(1, 2) << std::endl;\n"
                "    return 0;\n"
                "}\n"
            )
            % (name, name),
            "CMakeLists.txt": (
                "cmake_minimum_required(VERSION 3.16)\n"
                "project(%s VERSION 1.0.0 LANGUAGES CXX)\n"
                "set(CMAKE_CXX_STANDARD 17)\n"
                "add_library(%s src/%s.cpp)\n"
                "target_include_directories(%s PUBLIC include)\n"
                "add_executable(%s_app src/main.cpp)\n"
                "target_link_libraries(%s_app PRIVATE %s)\n"
            )
            % tuple([name] * 7),
            "README.md": "# %s\n\nC++ 项目骨架（mock 模式生成）。\n\n## 构建\n```bash\ncmake -B build && cmake --build build\n./build/%s_app\n```\n" % (name, name),
        }
    if language in ("ts", "javascript"):
        ext = "ts" if language == "ts" else "js"
        return {
            "package.json": (
                '{\n  "name": "%s",\n  "version": "1.0.0",\n'
                '  "scripts": { "start": "node src/index.%s" },\n'
                '  "license": "MIT"\n}\n'
            )
            % (name, ext),
            "src/index.%s" % ext: (
                "function add(a, b) { return a + b; }\n"
                "console.log('1+2=', add(1, 2));\n"
            ),
            "README.md": "# %s\n\n%s 项目骨架（mock 模式生成）。\n" % (name, language),
        }
    if language == "go":
        return {
            "go.mod": "module %s\n\ngo 1.21\n" % name,
            "main.go": (
                "package main\n\n"
                "import \"fmt\"\n\n"
                "func add(a, b int) int { return a + b }\n\n"
                "func main() { fmt.Println(\"1+2=\", add(1, 2)) }\n"
            ),
            "README.md": "# %s\n\nGo 项目骨架（mock 模式生成）。\n" % name,
        }
    if language == "rust":
        return {
            "Cargo.toml": '[package]\nname = "%s"\nversion = "0.1.0"\nedition = "2021"\n' % name,
            "src/main.rs": "fn add(a: i32, b: i32) -> i32 { a + b }\nfn main() { println!(\"1+2={}\", add(1, 2)); }\n",
            "README.md": "# %s\n\nRust 项目骨架（mock 模式生成）。\n" % name,
        }
    if language == "java":
        return {
            "src/main/java/App.java": (
                "public class App {\n"
                "    public static int add(int a, int b) { return a + b; }\n"
                "    public static void main(String[] args) {\n"
                "        System.out.println(\"1+2=\" + add(1, 2));\n"
                "    }\n}\n"
            ),
            "pom.xml": "<project xmlns=\"http://maven.apache.org/POM/4.0.0\">\n  <modelVersion>4.0.0</modelVersion>\n  <groupId>com.example</groupId>\n  <artifactId>%s</artifactId>\n  <version>1.0.0</version>\n</project>\n" % name,
            "README.md": "# %s\n\nJava 项目骨架（mock 模式生成）。\n" % name,
        }
    # python 默认
    return {
        "main.py": (
            "def add(a: float, b: float) -> float:\n"
            "    return a + b\n\n\n"
            "def main() -> None:\n"
            "    print('1+2=', add(1, 2))\n\n\n"
            "if __name__ == '__main__':\n"
            "    main()\n"
        ),
        "requirements.txt": "# 无第三方依赖\n",
        "README.md": "# %s\n\nPython 项目骨架（mock 模式生成）。\n\n## 运行\n```bash\npython main.py\n```\n" % name,
    }
