import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from .jmutils import PackagedJmClient
from jmcomic import *
import zhconv

class DbUtils:
    def __init__(self, client: PackagedJmClient):
        self.client = client.html # 限定只使用html客户端
        self.db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'albums.db')
        self.thread_num = 5
        self.semaphore = threading.Semaphore(self.thread_num)
        self.thread_local = threading.local()
        self.stop = False
        self.is_update = False

    def get_db(self):
        """获取线程本地的数据库连接"""
        if not hasattr(self.thread_local, "connection"):
            self.thread_local.connection = sqlite3.connect(self.db_path)
        return self.thread_local.connection

    def close_db(self):
        """关闭线程本地的数据库连接"""
        if hasattr(self.thread_local, "connection"):
            self.thread_local.connection.close()
            del self.thread_local.connection   
    
    def load_progress(self):
        """加载上次的进度"""
        conn = self.get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT last_id FROM progress WHERE id = 1')
        result = cursor.fetchone()
        return {'last_id': result[0] if result else 0}
    
    def save_progress(self, last_id):
        """保存当前进度"""
        conn = self.get_db()
        cursor = conn.cursor()
        # 检查数据库中是否已有值，且插入值是否大于数据库中的值
        cursor.execute('SELECT last_id FROM progress WHERE id = 1')
        result = cursor.fetchone()
        if (not result) or (last_id > result[0]):
            cursor.execute('INSERT OR REPLACE INTO progress (id, last_id) VALUES (1, ?)', (last_id,))
            conn.commit()

    def save_album(self, album_id, album_info):
        if album_info is None:
            return
        
        """保存单个本子信息"""
        conn = self.get_db()
        cursor = conn.cursor()
        
        # 将列表转换为JSON字符串
        works = ','.join(album_info['works']) if album_info['works'] else ''
        actors = ','.join(album_info['actors']) if album_info['actors'] else ''
        tags = ','.join(album_info['tags']) if album_info['tags'] else ''
        tags = ',' + tags + ','
        authors = ','.join(album_info['authors']) if album_info['authors'] else ''
        
        cursor.execute('''
        INSERT OR REPLACE INTO albums 
        (id, name, page_count, pub_date, update_date, likes, views, 
        comment_count, works, actors, tags, authors)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            album_id,
            zhconv.convert(album_info['name'], 'zh-hans').lower(),
            album_info['page_count'],
            album_info['pub_date'],
            album_info['update_date'],
            album_info['likes'],
            album_info['views'],
            album_info['comment_count'],
            works,
            zhconv.convert(actors, 'zh-hans').lower(),
            zhconv.convert(tags, 'zh-hans').lower(),
            zhconv.convert(authors, 'zh-hans').lower()
        ))
        
        conn.commit()

    def save_failed(self, album_id):
        conn = self.get_db()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO failed (id) VALUES (?)', (album_id,))
        conn.commit()

    def check_exist(self, album_id):
        conn = self.get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM albums WHERE id = ?', (album_id,))
        result = cursor.fetchone()
        return result is not None
    
    def fetch_album(self, album_id):
        thread_name = threading.current_thread().name
        try:
            if self.check_exist(album_id):
                print(f"\033[0;30;32m[jmcomic] {thread_name} [已存在] 本子 {album_id}\033[0m")
                self.save_progress(album_id)
                return
                
            album = self.client.get_album_detail(album_id)
            album_info = {
                'id': album_id,
                'name': album.name,
                'page_count': album.page_count,
                'pub_date': album.pub_date,
                'update_date': album.update_date,
                'likes': album.likes,
                'views': album.views,
                'comment_count': album.comment_count,
                'works': album.works,
                'actors': album.actors,
                'tags': album.tags,
                'authors': album.authors
            }
            self.save_album(album_id, album_info)
            self.save_progress(album_id)
            print(f"\033[0;30;32m[jmcomic] {thread_name} [成功] 获取本子 {album_id}: {album.name}\033[0m")
        except Exception as e:
            if "请求的本子不存在" in str(e):
                print(f"[jmcomic] {thread_name} [不存在] 本子 {album_id}")
            else:
                print(f"\033[0;30;31m[jmcomic] {thread_name} [失败] 获取本子 {album_id} 错误: {e}\033[0m")
                self.save_failed(album_id)
        finally:
            self.semaphore.release()
            self.close_db()  # 关闭线程本地的数据库连接

    def get_latest_id(self):
        page: JmCategoryPage = self.client.categories_filter(
            page=1,
            time=JmMagicConstants.TIME_ALL,
            category=JmMagicConstants.CATEGORY_SINGLE,
            order_by=JmMagicConstants.ORDER_BY_LATEST,
        )

        latest = 0

        for item in page:
            if int(item[0]) > latest:
                latest = int(item[0])

        return latest
    
    def update_db(self):
        if self.is_update:
            print("[jmcomic] 数据库更新中，跳过本次更新")
            return
        self.is_update = True
        # 加载进度
        progress = self.load_progress()
        start_id = progress['last_id'] + 1

        latest_id = self.get_latest_id()
        
        print(f"[jmcomic] 从ID {start_id} 开始爬取到 {latest_id}")

        self.executor = ThreadPoolExecutor(max_workers=self.thread_num, thread_name_prefix="Thread")
        
        try:
            for i in range(start_id, latest_id + 1):
                if self.stop:
                    break
                # 如果没有可用的线程，则等待
                self.semaphore.acquire()
                if self.stop:  # 再次检查stop状态
                    self.semaphore.release()
                    break
                self.executor.submit(self.fetch_album, i)

            # 等待所有任务完成
            self.executor.shutdown(wait=True)
            self.is_update = False
        except Exception as e:
            self.is_update = False
            print(f"[jmcomic] 更新出错，原因：{str(e)}")
            raise e
        finally:
            self.is_update = False

    def update_db_listed(self, update_list: list):
        if self.is_update:
            print("[jmcomic] 数据库更新中，跳过本次更新")
            return
        self.is_update = True

        print(f"[jmcomic] 爬取指定本子ID：{update_list}")
        
        self.executor = ThreadPoolExecutor(max_workers=self.thread_num, thread_name_prefix="Thread")

        try:
            for i in update_list:
                if self.stop:
                    break
                # 如果没有可用的线程，则等待
                self.semaphore.acquire()
                if self.stop:  # 再次检查stop状态
                    self.semaphore.release()
                    break
                self.executor.submit(self.fetch_album, i)

            # 等待所有任务完成
            self.executor.shutdown(wait=True)
            self.is_update = False
        except Exception as e:
            self.is_update = False
            print(f"[jmcomic] 更新出错，原因：{str(e)}")
            raise e
        finally:
            self.is_update = False

    def update_stop(self):
        self.stop = True