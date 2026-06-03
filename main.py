import asyncio
import os
import uuid
from io import BytesIO
from pathlib import Path
from typing import List, Tuple, Optional

import aiohttp
from PIL import Image

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import Image as BotImage, Plain
from astrbot.core.utils.astrbot_path import get_astrbot_data_path


# ==================== 配置常量 ====================
DEFAULT_TARGET_WIDTH = 1        # 默认输出图片宽度（像素）
MAX_GIF_COUNT = 20
MIN_GIF_COUNT = 2                 # gif张数最小值
MAX_TOTAL_DURATION = 60           # 总时长上限（秒）


@register(
    "astrbot_plugin_long_gif_converter",
    "xqe-bkflda",
    "将多张图片等分裁剪后组合成多个GIF（支持自定义宽度）",
    "2.0.0",
    "https://github.com/xqe-bkflda/astrbot_plugin_long_gif_converter"
)
class LongGifConverterPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        # 全局默认宽度，当命令未提供时使用（本次命令必须提供，所以此值仅作备用）
        self.default_width = self.config.get("target_width", DEFAULT_TARGET_WIDTH)

        self.plugin_name = "astrbot_plugin_long_gif_converter"
        data_root = get_astrbot_data_path()
        if isinstance(data_root, str):
            data_root = Path(data_root)
        self.data_dir = data_root / "plugin_data" / self.plugin_name
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.temp_dir = self.data_dir / "temp"
        self.temp_dir.mkdir(exist_ok=True)

        logger.info("长GIF转换插件已加载（支持自定义宽度）")

    # ==================== 辅助函数 ====================

    def _get_images_from_event(self, event: AstrMessageEvent) -> List[Tuple[str, bytes]]:
        """从消息事件中提取所有图片，返回 [(url或本地路径, 图片字节数据), ...]"""
        images = []
        for comp in event.message_obj.message:
            if isinstance(comp, BotImage):
                if comp.file and os.path.exists(comp.file):
                    with open(comp.file, 'rb') as f:
                        images.append((comp.file, f.read()))
                elif comp.url:
                    images.append((comp.url, None))
                elif comp.file and comp.file.startswith('base64://'):
                    import base64
                    base64_data = comp.file[9:]
                    img_bytes = base64.b64decode(base64_data)
                    images.append(("base64", img_bytes))
        return images

    async def _download_image(self, url: str) -> Optional[bytes]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=30) as resp:
                    if resp.status == 200:
                        return await resp.read()
                    else:
                        logger.error(f"下载图片失败: {resp.status}")
                        return None
        except Exception as e:
            logger.error(f"下载图片异常: {e}")
            return None

    def _resize_image(self, img: Image.Image, target_width: int) -> Image.Image:
        """等比例缩放图片到指定宽度，使用高质量 LANCZOS 算法"""
        original_width, original_height = img.size
        new_height = int(original_height * target_width / original_width)
        return img.resize((target_width, new_height), Image.Resampling.LANCZOS)

    def _crop_strip(self, img: Image.Image, y_start: int, height: int) -> Image.Image:
        return img.crop((0, y_start, img.width, y_start + height))

    def _create_gif(self, frames: List[Image.Image], duration_ms: int, output_path: str) -> bool:
        if not frames:
            return False
        try:
            first_frame = frames[0]
            for i, frame in enumerate(frames[1:]):
                if frame.size != first_frame.size:
                    frames[i+1] = frame.resize(first_frame.size, Image.Resampling.LANCZOS)

            first_frame.save(
                output_path,
                save_all=True,
                append_images=frames[1:],
                duration=duration_ms,
                loop=0,
                optimize=True
            )
            return True
        except Exception as e:
            logger.error(f"GIF 生成失败: {e}")
            return False

    # ==================== 核心处理逻辑 ====================

    async def _process_images(
        self,
        images_data: List[bytes],
        gif_count: int,
        frame_duration: float,
        target_width: int
    ) -> Tuple[List[str], List[str]]:
        """处理图片，生成 GIF，返回 (gif_paths, temp_files)"""
        temp_files = []
        gif_paths = []

        # 1. 解码并处理所有图片
        processed_images = []
        for img_bytes in images_data:
            try:
                img = Image.open(BytesIO(img_bytes))
                # 转换为 RGB
                if img.mode in ('RGBA', 'LA', 'P'):
                    rgb_img = Image.new('RGB', img.size, (255, 255, 255))
                    if img.mode == 'P':
                        img = img.convert('RGBA')
                    if img.mode in ('RGBA', 'LA'):
                        rgb_img.paste(img, mask=img.split()[-1])
                    else:
                        rgb_img.paste(img)
                    img = rgb_img
                elif img.mode != 'RGB':
                    img = img.convert('RGB')

                img = self._resize_image(img, target_width)
                processed_images.append(img)
            except Exception as e:
                logger.error(f"图片处理失败: {e}")
                continue

        if not processed_images:
            return [], temp_files

        # 2. 计算裁剪高度
        min_height = min(img.height for img in processed_images)
        strip_height = min_height // gif_count
        if strip_height == 0:
            logger.error(f"图片高度不足，无法切分成 {gif_count} 份 (最小高度={min_height}, 要求高度>={gif_count})")
            return [], temp_files

        # 3. 对每张图片裁剪条带，按列组织
        strips_per_image = []
        for img in processed_images:
            strips = [self._crop_strip(img, j * strip_height, strip_height) for j in range(gif_count)]
            strips_per_image.append(strips)

        # 4. 按列组合成 GIF
        for j in range(gif_count):
            frames = [strips_per_image[i][j] for i in range(len(processed_images))]
            gif_filename = f"gif_{uuid.uuid4().hex}.gif"
            gif_path = str(self.temp_dir / gif_filename)
            if self._create_gif(frames, int(frame_duration * 1000), gif_path):
                gif_paths.append(gif_path)
                temp_files.append(gif_path)

        return gif_paths, temp_files

    # ==================== 命令入口 ====================

    @filter.command("长gif转换")
    async def convert_command(self, event: AstrMessageEvent):
        """
        命令格式: /长gif转换 <gif张数> <gif裁剪宽度> <gif轮播速度>
        需要引用或附带多张图片
        """
        # 1. 解析命令参数
        full_text = ""
        for comp in event.message_obj.message:
            if isinstance(comp, Plain):
                full_text += comp.text

        parts = full_text.strip().split()
        if len(parts) < 4:
            yield event.plain_result(
                "用法：/长gif转换 <gif张数> <gif裁剪宽度> <gif轮播速度>\n"
                "示例：/长gif转换 5 200 0.5\n"
                "需要引用或附带多张图片\n"
                f"参数说明：\n"
                f"  gif张数: {MIN_GIF_COUNT}~{MAX_GIF_COUNT}\n"
                f"  gif裁剪宽度: 建议 100~500 像素\n"
                f"  gif轮播速度: 每帧停留秒数，总时长不能超过 {MAX_TOTAL_DURATION} 秒"
            )
            return

        try:
            gif_count = int(parts[1])
            target_width = int(parts[2])
            frame_duration = float(parts[3])
        except ValueError:
            yield event.plain_result("参数格式错误：gif张数和裁剪宽度必须是整数，轮播速度必须是数字（秒）")
            return

        # 参数校验
        if gif_count < MIN_GIF_COUNT:
            yield event.plain_result(f"gif张数不能小于 {MIN_GIF_COUNT}")
            return
        if gif_count > MAX_GIF_COUNT:
            yield event.plain_result(f"gif张数不能大于 {MAX_GIF_COUNT}")
            return
        if target_width < 50:
            yield event.plain_result("裁剪宽度太小，建议至少 50 像素")
            return
        if target_width > 1000:
            yield event.plain_result("裁剪宽度太大，建议不超过 1000 像素，以免文件过大")
            return
        if frame_duration <= 0:
            yield event.plain_result("轮播速度必须为正数")
            return

        total_duration = frame_duration * gif_count
        if total_duration > MAX_TOTAL_DURATION:
            yield event.plain_result(
                f"轮播速度({frame_duration}秒) × gif张数({gif_count}) = {total_duration}秒 > {MAX_TOTAL_DURATION}秒，请降低轮播速度或减少gif张数"
            )
            return

        # 2. 获取图片
        images = self._get_images_from_event(event)
        # 检查引用消息中的图片
        for comp in event.message_obj.message:
            if hasattr(comp, 'chain') and comp.chain:
                for sub_comp in comp.chain:
                    if isinstance(sub_comp, BotImage):
                        images.append((sub_comp.url or sub_comp.file, None))

        if not images:
            yield event.plain_result("未找到图片，请引用或附带至少一张图片")
            return

        # 3. 发送处理中提示
        yield event.plain_result(f"正在处理... |{len(images)}|{gif_count}|{target_width}px| ,请稍候...")

        # 4. 下载所有图片
        image_data_list = []
        for url_or_path, img_bytes in images:
            if img_bytes is not None:
                image_data_list.append(img_bytes)
            else:
                downloaded = await self._download_image(url_or_path)
                if downloaded:
                    image_data_list.append(downloaded)

        if not image_data_list:
            yield event.plain_result("图片下载失败，请检查图片链接")
            return

        # 5. 处理图片并生成 GIF
        try:
            gif_paths, temp_files = await self._process_images(
                image_data_list, gif_count, frame_duration, target_width
            )

            if not gif_paths:
                yield event.plain_result("GIF 生成失败，请检查图片尺寸是否足够（图片高度需 ≥ gif张数）")
                return

            # 6. 发送 GIF
            chain = [BotImage.fromFileSystem(path) for path in gif_paths]
            chain.append(Plain(f"ˣ"))
            yield event.chain_result(chain)

            # 7. 延迟清理临时文件
            if temp_files:
                asyncio.create_task(self._delayed_cleanup(temp_files))

        except Exception as e:
            logger.error(f"GIF 生成失败: {e}")
            yield event.plain_result(f"GIF 生成失败: {e}")

    async def _delayed_cleanup(self, files: List[str], delay: int = 10):
        await asyncio.sleep(delay)
        for path in files:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception as e:
                logger.error(f"清理临时文件失败 {path}: {e}")

    async def terminate(self):
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        logger.info("长GIF转换插件已卸载")