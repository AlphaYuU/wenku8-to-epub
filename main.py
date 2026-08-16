"""
从wenku8.net下载小说，并按卷/整本转epub
"""
import os
import sys
from datetime import datetime

from PIL import Image

from create_epub import Epub, XML_TITLE_LABEL, XML_PARAGRAPH_LABEL, XML_IMAGE_LABEL
from wenku8 import Wenku8Download

# --------自定义参数------------

# epub存储目录（相对路径/绝对路径）
save_epub_dir = 'epub'
# 每次网络请求后停顿时间，避免封IP
sleep_time = 4
# 是否将插图第一页设为封面，若不设置就默 认使用小说详情页封面
use_divimage_set_cover = True
# 指定wenku8的hostname，可填www.wenku8.net www.wenku8.cc www.wenku8.com
wenku_host = 'www.wenku8.com'
# 反代pic.wenku8.com、app.wenku8.com的hostname：xxxx.xxxx.workers.dev 或 自定义域名
wenkupic_proxy_host = None
wenkuapp_proxy_host = None
# 使用系统 Chrome 访问受 Cloudflare 保护的网页；浏览器配置保存在项目目录内
use_browser = True
# 是否在后台无界面运行 Chrome；False 表示保留可操作的浏览器窗口
browser_headless = False
# 可见模式下是否最小化启动 Chrome
browser_start_minimized = False
# Chrome 启动和自动跳转后把输入焦点还给 CMD
browser_keep_console_focused = True
# 等待用户在 Chrome 中完成 Cloudflare 验证的最长时间（秒）
browser_wait_timeout = 180
# 临时 HTTP 403/429/服务器错误的最大尝试次数与首次等待时间（秒）
browser_max_retries = 5
browser_retry_base_delay = 8
# ---------------------------


class TeeStream:
    """同时把控制台输出写入运行日志。"""

    def __init__(self, console, log_file):
        self.console = console
        self.log_file = log_file

    def write(self, text):
        written = self.console.write(text)
        self.log_file.write(text)
        return written

    def flush(self):
        self.console.flush()
        self.log_file.flush()

    def __getattr__(self, name):
        return getattr(self.console, name)


def run_with_log():
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, 'last-run.log')
    original_stdout, original_stderr = sys.stdout, sys.stderr
    with open(log_path, 'w', encoding='utf-8', buffering=1) as log_file:
        log_file.write(f'Run started: {datetime.now().isoformat(timespec="seconds")}\n')
        sys.stdout = TeeStream(original_stdout, log_file)
        sys.stderr = TeeStream(original_stderr, log_file)
        try:
            return main()
        finally:
            sys.stdout = original_stdout
            sys.stderr = original_stderr


if not os.path.exists(save_epub_dir):
    os.makedirs(save_epub_dir)


def download_volume(book_epub, it, include_volume_in_toc=False):
    """下载一卷，并按需在 EPUB 目录中保留卷级目录。"""
    print('Start making volume:', wk.book['title'], it['volume'])
    for chapter_title, chapter_href in it['chapter']:
        content_title, content_list, image_urls = wk.get_chapter(chapter_href)
        if wk.error_msg:
            print('Error:', wk.error_msg)
            return False

        # 设置HTML格式
        html_body = XML_TITLE_LABEL.format(ct=chapter_title)
        if content_list:
            print('├── Start downloading chapter-text:', chapter_title)
            for p in content_list:
                html_body += XML_PARAGRAPH_LABEL.format(p=p)
            print('│   └── Download chapter-text completed.')
        elif image_urls:
            print('├── Start downloading chapter-image:', chapter_title)
            for img_url in image_urls:
                file_path, file_name, file_base = wk.save_image(img_url)
                if file_name:
                    if use_divimage_set_cover and not book_epub.is_set_cover:  # 将插图的第一张长图作为封面
                        with Image.open(file_path) as img:
                            width, height = img.size
                        if width <= height:
                            book_epub.set_cover(file_path)
                    book_epub.set_images(file_path)
                    html_body += XML_IMAGE_LABEL.format(fb=file_base, fn=file_name)
                print('│   ├──', img_url, '->', file_path, 'success' if file_name else 'fail')
            print('│   └── Download chapter-image completed.')
        else:
            print('├── Downloaded empty chapter.')

        # 多卷合并为整本时保留卷级目录；单卷 EPUB 使用扁平目录。
        volume_title = it['volume'] if include_volume_in_toc else None
        book_epub.set_html(chapter_title, html_body, volume_title)

    if not book_epub.is_set_cover:  # 插图第一张图片未能设置为封面，就把缩略图作为封面
        book_epub.set_cover('src/cover.jpg')
    return True


def whole_book_download():
    """整本下载"""
    book_epub = Epub()
    book_epub.set_metadata(wk.book['title'], author=wk.book['author'], desp=wk.book['description'],
                           publisher=wk.book['publisher'], source_url=wk.book['api']['detail'],
                           tag_list=wk.book['tags'], vol_idx=1,
                           cover_path='src/cover.jpg' if not use_divimage_set_cover else None)

    # 只有多卷整本才需要“卷 -> 章”两级目录；仅一卷时直接显示章节。
    include_volume_in_toc = len(wk.book['toc']) > 1
    for it in wk.book['toc']:
        flag = download_volume(book_epub, it, include_volume_in_toc=include_volume_in_toc)
        if not flag:
            return False
        print('└── Making volume completed.\n')

    book_epub.pack_book(save_epub_dir)
    wk.clear_src()
    return True


def volume_by_volume_download():
    """按卷下载，单独下载某一/些卷"""
    print_format([it['volume'] for it in wk.book['toc']])
    volume_idx_list = input('输入要下载的卷索引，下载多卷用空格分割（默认0，逐卷下载）：').split()
    print()
    # 检查输入索引是否合法
    if volume_idx_list and all(map(lambda i: i.isdigit() and (1 <= int(i) <= len(wk.book['toc'])), volume_idx_list)):
        volume_idx_list = sorted(map(int, volume_idx_list))
    elif volume_idx_list == [] or '0' in volume_idx_list:
        volume_idx_list = list(map(lambda i: i + 1, range(len(wk.book['toc']))))
    else:
        print('Error: volume_id is invalid.')
        return False

    vol_idx = 0
    for it in wk.book['toc']:
        vol_idx += 1
        if vol_idx not in volume_idx_list:
            continue

        wk.image_idx = 0

        book_epub = Epub()
        book_epub.set_metadata(wk.book['title'], it['volume'], author=wk.book['author'], desp=wk.book['description'],
                               publisher=wk.book['publisher'], source_url=wk.book['api']['detail'],
                               tag_list=wk.book['tags'], vol_idx=vol_idx,
                               cover_path='src/cover.jpg' if not use_divimage_set_cover else None)

        flag = download_volume(book_epub, it, include_volume_in_toc=False)
        if not flag:
            return False

        book_epub.pack_book(save_epub_dir)
        print('└── Packing volume completed.\n')
        wk.clear_src()
    return True


def print_format(volume_list):
    """格式化打印每卷标题"""
    max_chars_per_line = 55  # 每行的最大字符数
    max_unit_len = max([len(it) for it in volume_list])
    max_ele_per_line_num = max_chars_per_line // (max_unit_len + 4)

    template = "{0:>2d}: {1:{2}<{3}s}\t"

    total_text = ""
    current_line = ""
    current_line_num = 0
    for idx, volume_title in enumerate(volume_list):
        current_line_num += 1
        if current_line_num > max_ele_per_line_num:
            total_text += current_line.rstrip() + '\n'
            current_line_num = 1
            current_line = template.format(idx + 1, volume_title, chr(12288), max_unit_len)  # 使用chr(12288)填充
        else:
            current_line += template.format(idx + 1, volume_title, chr(12288), max_unit_len)

    total_text += current_line
    print(total_text)


def download_one_book(book_id):
    global wk
    wk = None
    try:
        wk = Wenku8Download(
            book_id,
            wenku_host,
            wenkupic_proxy_host,
            wenkuapp_proxy_host,
            use_browser=use_browser,
            browser_headless=browser_headless,
            browser_start_minimized=browser_start_minimized,
            browser_keep_console_focused=browser_keep_console_focused,
            browser_wait_timeout=browser_wait_timeout,
            browser_max_retries=browser_max_retries,
            browser_retry_base_delay=browser_retry_base_delay,
        )
        if wk.error_msg:
            print('Error:', wk.error_msg)
            return False
        wk.sleep_time = sleep_time  # 设置延迟时间
        wk.wka.sleep_time = sleep_time

        print('Light Noval Title:', wk.book['title'], '\n')

        mode = input('选择下载模式：0-按卷下载（默认）；1-整本下载。\n输入模式索引：')
        print()

        if not wk.book['copyright']:
            print('Note: web copyright is restricted and will be downloaded from APP.\n')

        if not mode:
            mode = '0'
        if mode.isdigit() and int(mode) == 0:
            return volume_by_volume_download()
        elif mode.isdigit() and int(mode) == 1:
            return whole_book_download()
        else:
            print('Error: mode_id is invalid.')
            return False
    except Exception as exc:
        print(f'Error: {exc}')
        return False
    finally:
        if wk is not None:
            wk.close()
            wk = None


def main():
    print('轻小说文库 EPUB 下载器。输入 q 可退出。\n')
    while True:
        try:
            book_id = input(
                f'输入要下载的小说id（如 https://{wenku_host}/book/2906.htm 的id是2906）：'
            ).strip()
        except (EOFError, KeyboardInterrupt):
            print('\n已退出。')
            return 0

        print()
        if book_id.lower() in {'q', 'quit', 'exit'}:
            print('已退出。')
            return 0
        if not book_id.isdigit():
            print('Error: book_id is invalid.\n')
            continue

        succeeded = download_one_book(book_id)
        if succeeded:
            print('\n本次导出完成，已返回开始状态，可以继续输入另一本小说的 ID。\n')
        else:
            print('\n本次任务未完成，已返回开始状态；可以重试或输入另一本小说的 ID。\n')


if __name__ == '__main__':
    sys.exit(run_with_log())
