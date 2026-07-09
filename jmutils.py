import os, shutil, img2pdf, zipfile, jmcomic
from PIL import Image
import cv2
from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
import math
from urllib.parse import urlencode
from pathlib import Path
import zhconv


def compress_image(images):
    for image in images:
        img = cv2.imread(image)
        if img.shape[0] > 2000:
            img = cv2.resize(img, (int(img.shape[1] * 2000 / img.shape[0]), 2000))
        cv2.imwrite(image, img, [cv2.IMWRITE_JPEG_QUALITY, 30])


def zip_manga(album_id, password="", file_suffix=".zip"):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    album_dir = os.path.join(script_dir, f"manga/{album_id}")
    compressed_dir = os.path.join(script_dir, f"compressed/{album_id}")
    # 获取album_dir文件夹的大小，如果album_size大于30MB，则压缩图片
    album_size = sum(
        sum(os.path.getsize(os.path.join(parent, file)) for file in files)
        for parent, dirs, files in os.walk(album_dir)
    )
    print("[jmcomic]原始本子大小：", album_size)
    need_compress = False
    if album_size > 30 * 1024 * 1024:
        need_compress = True
        print("[jmcomic]需要压缩")
    # Convert every photo into a PDF file.
    for photo in os.listdir(album_dir):
        output = f"{album_dir}/{photo}.pdf"
        photo_dir = os.path.join(album_dir, photo)
        images = [os.path.join(photo_dir, image) for image in os.listdir(photo_dir)]
        images.sort()
        if need_compress:
            compress_image(images)
        with open(output, "wb") as f:
            f.write(img2pdf.convert(images))


    '''
        Modify the code below yourself to zip the PDF files with encryption.
    '''

    # Zip the PDF files without encryption.
    with zipfile.ZipFile(f"{compressed_dir}.zip", "w") as zipf:
        for pdf in os.listdir(album_dir):
            if pdf.endswith(".pdf"):
                zipf.write(f"{album_dir}/{pdf}", pdf)


def delete_temp_file(album_id: int):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    album_dir = os.path.join(script_dir, f"manga/{album_id}")
    if os.path.exists(album_dir):
        shutil.rmtree(album_dir)


class JmAlbumInfoPic:
    def __init__(self):
        # 初始化Firefox浏览器
        self.options = webdriver.FirefoxOptions()
        self.options.add_argument("--headless")  # 无头模式
        self.options.add_argument("--disable-gpu")
        self.options.add_argument("--width=700")
        self.options.add_argument("--height=2000")
        self.options.set_preference("app.update.auto", False)
        self.options.set_preference("app.update.enabled", False)
        self.browser_driver = webdriver.Firefox(options=self.options)

    def __del__(self):
        self.browser_driver.quit()

    def generate(self, album: jmcomic.JmAlbumDetail):
        script_dir = Path(__file__).resolve().parent
        jacket_path = script_dir / f"jacket_{album.album_id}.jpg"
        full_page_path = script_dir / f"full_page_{album.album_id}.png"
        try:
            driver = self.browser_driver
            # 寻找 manga/{album_id}/1/ 里序号最小的图片作为封面
            chapter_dir = script_dir / "manga" / str(album.album_id) / "1"
            min_image = min(os.listdir(chapter_dir))
            shutil.copy(chapter_dir / min_image, jacket_path)

            query = urlencode(
                {
                    "album_id": album.album_id,
                    "title": album.title or "",
                    "tags": ",".join(album.tags or []),
                },
                encoding="utf-8",
            )
            url = f"{(script_dir / 'info.html').as_uri()}?{query}"
            driver.get(url)

            # 等待封面图片加载完成后再截图
            WebDriverWait(driver, 10).until(
                lambda d: d.execute_script(
                    "const img = document.querySelector('.cover');"
                    "return img && img.complete && img.naturalWidth > 0;"
                )
            )

            # 获取container元素
            container = driver.find_element("css selector", ".container")

            # 获取设备像素比（处理高DPI屏幕）
            dpr = driver.execute_script("return window.devicePixelRatio")

            # 获取元素位置和尺寸
            location = container.location
            size = container.size

            # 计算裁剪区域（考虑设备像素比）
            left = math.floor(location["x"] * dpr) - 15
            top = math.floor(location["y"] * dpr) - 15
            right = math.floor((location["x"] + size["width"]) * dpr) + 15
            bottom = math.floor((location["y"] + size["height"]) * dpr) + 15

            # 截取完整页面截图
            driver.save_screenshot(str(full_page_path))

            # 打开截图并裁剪
            image = Image.open(full_page_path)
            image = image.crop((left, top, right, bottom))

            # 转换为RGB模式
            if image.mode == "RGBA":
                image = image.convert("RGB")

            # 保存裁剪后的图片
            save_path = script_dir / "info_image" / f"{album.album_id}.jpg"
            save_path.parent.mkdir(parents=True, exist_ok=True)
            image.save(save_path)
        except Exception as e:
            print(f"生成封面图片失败: {e}")
        finally:
            for path in (full_page_path, jacket_path):
                if path.exists():
                    path.unlink()
            return None


def negtag_to_sql(negative_tags):
    if not negative_tags:
        return ""
    return " AND ".join([f"tags NOT LIKE '%,{tag},%'" for tag in negative_tags])


class RuleLogicExpression:
    class Node:
        def __init__(self, value):
            self.value = value
            self.left = None
            self.right = None

    def __init__(self, attr, exp):
        if attr not in ["name", "tags", "authors"]:
            raise ValueError(
                f"Invalid attribute {attr}, must be one of: name, tags, authors"
            )
        self.attr = attr
        self.precise = attr == "tags"
        self.exp = exp
        self.parse()

    def parse(self):
        self.exp = "(" + self.exp + ")"
        symbols = ["&", "|", "!", "(", ")"]
        priority = {"!": 3, "&": 2, "|": 1, "(": 0}
        symbol_stack = []
        value_stack = []
        value = ""
        raw_symbol = False
        for c in self.exp:
            if raw_symbol:
                raw_symbol = False
                value += c
                continue
            if c == "\\":
                raw_symbol = True
                continue
            if c == "（":
                c = "("
            elif c == "）":
                c = ")"
            elif c == "'":
                c = "''"
            if c in symbols:
                if value:
                    value_stack.append(self.Node(value))
                    value = ""
                if c == "(":
                    symbol_stack.append(c)
                elif c == ")":
                    while symbol_stack[-1] != "(":
                        new_node = self.Node(symbol_stack.pop())
                        if new_node.value == "!":
                            new_node.right = value_stack.pop()
                        else:
                            new_node.right = value_stack.pop()
                            new_node.left = value_stack.pop()
                        value_stack.append(new_node)
                    symbol_stack.pop()
                else:
                    while priority[symbol_stack[-1]] >= priority[c]:
                        new_node = self.Node(symbol_stack.pop())
                        if new_node.value == "!":
                            new_node.right = value_stack.pop()
                        else:
                            new_node.right = value_stack.pop()
                            new_node.left = value_stack.pop()
                        value_stack.append(new_node)
                    symbol_stack.append(c)
            else:
                value += c
        if value_stack:
            self.root = value_stack.pop()
        else:
            self.root = None
        if value_stack or symbol_stack:
            raise ValueError("Invalid expression")

    def to_sql(self) -> str:
        def dfs(node):
            if node.value == "!":
                return f"NOT ({dfs(node.right)})"
            elif node.value == "&":
                return f"({dfs(node.left)}) AND ({dfs(node.right)})"
            elif node.value == "|":
                return f"({dfs(node.left)}) OR ({dfs(node.right)})"
            else:
                if self.precise:
                    return f"{self.attr} LIKE '%,{zhconv.convert(node.value.lower(), 'zh-hans')},%'"
                else:
                    return f"{self.attr} LIKE '%{zhconv.convert(node.value.lower(), 'zh-hans')}%'"

        return dfs(self.root)


class PackagedJmClient:
    def __init__(self, html: jmcomic.JmHtmlClient, api: jmcomic.JmApiClient):
        self.html = html
        self.api = api

    def get_album_detail(self, comic_id: int | str) -> jmcomic.JmAlbumDetail:
        try:
            album = self.html.get_album_detail(comic_id)
            return album
        except Exception:
            print("[jmcomic]获取本子信息：html接口失败，切换至api接口")
            album = self.api.get_album_detail(comic_id)
            return album


class PackagedJmOption:
    def __init__(self, html: jmcomic.JmOption, api: jmcomic.JmOption):
        self.html = html
        self.api = api
        self.client = None

    def build_jm_client(self) -> PackagedJmClient:
        if not self.client:
            self.client = PackagedJmClient(
                self.html.build_jm_client(), self.api.build_jm_client()
            )
        return self.client


def packaged_download_album(
    option: PackagedJmOption, album: jmcomic.JmAlbumDetail
):
    try:
        with jmcomic.new_downloader(option.html) as dler:
            dler.download_by_album_detail(album)
            dler.raise_if_has_exception()
    except Exception:
        print("[jmcomic]下载本子：html接口失败，切换至api接口")
        with jmcomic.new_downloader(option.api) as dler:
            dler.download_by_album_detail(album)
            dler.raise_if_has_exception()


if __name__ == "__main__":
    option = jmcomic.create_option_by_file(
        os.path.join(os.path.dirname(__file__), "option.yml")
    )
    client = option.new_jm_client()
    album = client.get_album_detail(810872)
    print(album.page_count)
