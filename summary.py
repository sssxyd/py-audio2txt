from concurrent.futures import ThreadPoolExecutor
import datetime
import logging
import os
import sys
import threading
from modelscope import AutoModelForCausalLM, AutoTokenizer
import torch
from common import get_duration, get_executable_directory, load_args

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

class TextSummarizer:
    def __init__(self, template : str, verbose : bool = False, overwrite : bool = False):
        self.template = template
        self.verbose = verbose
        self.overwrite = overwrite

        if self.verbose:
            logger.info("使用的摘要模板:")
            logger.info(self.template)

        model_path = "Qwen/Qwen2.5-7B-Instruct"
        device="cuda" if torch.cuda.is_available() else "cpu"
        self.tokenizer = AutoTokenizer.from_pretrained(
            model_path, 
            trust_remote_code=True,
        )
        
        # 使用device_map自动分配设备[3](@ref)
        self.model = AutoModelForCausalLM.from_pretrained(
            model_path,
            device_map=device,
            torch_dtype=torch.bfloat16,
            trust_remote_code=True
        )
        
        # 动态获取模型真实支持长度
        self.max_ctx_length = self.model.config.max_position_embeddings  
        # 摘要长度按比例调整
        self.summary_max_length = min(self.max_ctx_length // 4, 512)  
        logger.info(f"模型最大上下文长度: {self.max_ctx_length}")
        logger.info(f"模型摘要长度: {self.summary_max_length}")

    def _truncate_text(self, text : str):
        """智能截断文本以适应模型上下文窗口"""
        tokens = self.tokenizer.encode(text)
        if len(tokens) > self.max_ctx_length:
            # 保留首尾重要信息（开头50% + 结尾30%）
            keep_tokens = tokens[:self.max_ctx_length//2] + tokens[-self.max_ctx_length//3 * 2:]
            return self.tokenizer.decode(keep_tokens[:self.max_ctx_length])
        return text

    def generate_summary(self, txt_path : str):
        """生成文本摘要的核心方法"""
        try:
            # 读取并预处理文本
            with open(txt_path, 'r', encoding='utf-8') as f:
                raw_text = f.read().strip()
            
            if not raw_text:
                return "错误：文件内容为空"

            processed_text = self._truncate_text(raw_text)

            prompt = self.template.replace("${text}", processed_text)

            # 生成摘要
            inputs = self.tokenizer(prompt, return_tensors="pt").to(self.model.device)
            outputs = self.model.generate(**inputs,
                max_new_tokens=self.summary_max_length,
                temperature=0.6,
                top_k=50,
                top_p=0.9,
                do_sample=True,
                repetition_penalty=1.2
            )

            # 后处理输出
            full_response = self.tokenizer.decode(outputs[0], skip_special_tokens=True)
            return full_response.split("assistant")[1].strip()

        except Exception as e:
            logger.error(f"处理文件 {txt_path} 时发生错误: {str(e)}")
            return ""
        
    def is_text_file(self, file_path : str):
        if file_path.endswith(".txt"):
            return True
        return False  
        
    def is_summary_file(self, file_path : str):
        if file_path.endswith(".txt.md"):
            return True
        return False   
    
    def collect_txt_files(self, paths: list[str]) -> list[str]:
        file_queue = []
        result_lock = threading.Lock()
        txt_files = []

        if len(paths) == 0:
            logger.error("没有提供文件或目录路径")
            return txt_files

        threads = os.cpu_count() * 2
        logger.info(f"使用 {threads} 个线程扫描目录")

        # 第一阶段：多线程遍历目录结构
        def scan_dirs(path):
            if os.path.isfile(path):
                file_queue.append(path)
            else:
                for root, _, files in os.walk(path):
                    file_queue.extend(os.path.join(root, f) for f in files)

        with ThreadPoolExecutor(max_workers=threads) as dir_executor:
            dir_executor.map(scan_dirs, paths)

        # 第二阶段：多线程检测音频文件
        def check_file(file_path):
            if self.is_summary_file(file_path):
                with result_lock:
                    txt_files.append(os.path.abspath(file_path))

        with ThreadPoolExecutor(max_workers=threads) as file_executor:
            file_executor.map(check_file, file_queue)

        return txt_files    
        
    def scan_and_summarize(self, pathes):
        """扫描目录并生成摘要"""
        if not pathes:
            logger.error("没有提供文件或目录路径")
            return

        txt_files = self.collect_txt_files(pathes)
        if not txt_files:
            logger.error("没有找到有效的文本文件")
            return
        logger.info(f"找到 {len(txt_files)} 个有效文本文件")
        count = 0
        for txt_file in txt_files:
            output_file = os.path.abspath(txt_file) + ".md"
            if os.path.exists(output_file) and not self.overwrite:
                logger.info(f"摘要文件已存在: {output_file}, 跳过")
                continue
            if self.verbose:
                logger.info(f"正在处理文件: {txt_file}")
            summary = self.generate_summary(txt_file)
            if summary == "":
                logger.error(f"文件 {txt_file} 处理失败")
                continue
            count += 1
            if self.verbose:
                logger.info(f"摘要内容: {summary}")
            if os.path.exists(output_file):
                os.remove(output_file)
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(summary)
            if self.verbose:
                logger.info(f"文件{txt_file}的摘要已保存到: {output_file}")
        logger.info(f"共计 {len(txt_files)} 个文本文件，成功对 {count} 个文件生成摘要")
        return txt_files

# 使用示例
if __name__ == "__main__":
    start_time = datetime.datetime.now()
    options, params = load_args()
    if "v" in options or "version" in options:
        print("summary Version: 1.1.0")
        print("Author: sssxyd@gmail.com")
        print("Repo: https://github.com/sssxyd/py-audio2txt")
        print("License: Apache-2.0")
        exit(0)
    if "h" in options or "help" in options or len(params) == 0:
        print("Usage: summary [options] <txt_file> <txt_dir> ...")
        print("Options:")
        print("  -v, --version   Show version")
        print("  -h, --help      Show this help message")
        print("  -l, --log-level Log level (default: INFO)")
        print("  -t, --template   Summary template file (default: template.txt)")
        print("  --overwrite   Overwrite existing file")
        print("  --verbose   Verbose mode")
        exit(0)    
    log_level = options.get("l", options.get("log-level", "INFO")).upper()
    prompt_file = options.get("t", options.get("template", ""))
    if prompt_file == "":
        prompt_file = os.path.join(get_executable_directory(), "template.txt")
    if not os.path.exists(prompt_file):
        logger.error(f"摘要模板文件不存在: {prompt_file}")
        exit(1)
    with open(prompt_file, 'r', encoding='utf-8') as f:
        prompt = f.read().strip()
    if not prompt:
        logger.error(f"摘要模板文件内容为空: {prompt_file}")
        exit(1)
    
    summarizer = TextSummarizer(template=prompt, verbose=options.get("verbose", False), overwrite=options.get("overwrite", False))
    summarizer.scan_and_summarize(params)
    logger.info(f"总耗时: {get_duration(start_time)}")
    logger.info("处理完成，感谢使用！")