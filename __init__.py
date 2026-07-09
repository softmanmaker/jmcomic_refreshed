import jmcomic
from .jmutils import *
from .dbutils import DbUtils
from nonebot import on_command, require, get_driver
from nonebot.adapters.onebot.v11 import (
    Bot,
    Message,
    GroupMessageEvent,
    PrivateMessageEvent,
    MessageSegment,
    Event,
)
from nonebot.params import CommandArg
from nonebot.matcher import Matcher
from nonebot.permission import SUPERUSER
import os
import asyncio
from dataclasses import dataclass
import time, json, zhconv, sqlite3, math
from pathlib import Path

# 创建线程池
semaphore = asyncio.Semaphore(5)  # 任务计数
downloading_manga = set()  # 正在下载的本子ID

# 创建命令处理器
comic = on_command("manga", priority=5, block=True)
suffix = on_command("manga_suffix", priority=5, block=True, permission=SUPERUSER)
image_set = on_command("manga_maximages", priority=5, block=True, permission=SUPERUSER)
credit = on_command("manga_credit", priority=5, block=True, permission=SUPERUSER)
negtags = on_command("manga_negtag", priority=5, block=True, permission=SUPERUSER)
cooldown = on_command("manga_cooldown", priority=5, block=True, permission=SUPERUSER)
white_list = on_command("manga_whitelist", priority=5, block=False, permission=SUPERUSER)
baneng = on_command("manga_baneng", priority=5, block=True, permission=SUPERUSER)
updatedb = on_command("manga_updatedb", priority=5, block=True, permission=SUPERUSER)

# 全局配置
script_dir = os.path.dirname(os.path.abspath(__file__))
conn = sqlite3.connect(os.path.join(script_dir, "albums.db"))  # 创建数据库连接

config_path = os.path.join(script_dir, "settings.json")
credits_path = os.path.join(script_dir, "credits.json")
with open(config_path, "r", encoding="utf-8") as f:
    config = json.load(f)
with open(credits_path, "r", encoding="utf-8") as f:
    credits = json.load(f)
CD_SECONDS = config["cd_seconds"]
file_suffix = config["file_suffix"]
max_image_count = config["max_image_count"]
negative_tags = [
    zhconv.convert(tag, "zh-hans").lower() for tag in config["negative_tags"]
]
ban_english = config["ban_english"]
whitelist = config["whitelist"]
superadmin = config["superadmin"]
driver = get_driver()

# 数据库更新状态
is_updating_db = False

# 全局变量
processing = False  # 是否有正在处理的请求
queue = None  # 请求队列
last_request_time = {}  # 记录每个群的最后请求时间
queue_started = False  # 队列是否已启动
html = jmcomic.create_option_by_file(os.path.join(script_dir, "options/html.yml"))
api = jmcomic.create_option_by_file(os.path.join(script_dir, "options/api.yml"))
option = PackagedJmOption(html, api)
info_pic = JmAlbumInfoPic()
db_utils = DbUtils(option.build_jm_client())


@driver.on_shutdown
def cleanup():
    print("[jmcomic] 插件正在关闭")
    global info_pic, db_utils
    print("[jmcomic] 关闭info_pic")
    if info_pic:
        del info_pic
    print("[jmcomic] 关闭db_utils")
    if db_utils:
        db_utils.update_stop()
        print("[jmcomic] 等待数据库更新完成")
        while db_utils.is_update:
            time.sleep(0.1)
    print("[jmcomic] 插件已关闭")


async def asyncio_wrapper(
    func, *args, **kwargs
):  # 协程套皮，防止事件响应函数阻塞（主要用于给jmcomic库中不支持协程的功能套皮）
    return func(*args, **kwargs)


@dataclass
class DownloadTask:
    album: jmcomic.JmAlbumDetail
    event: GroupMessageEvent | PrivateMessageEvent  # 保存原始事件

async def send_to_user(bot: Bot, event: PrivateMessageEvent, message: str):
    """发送消息到指定用户"""
    await bot.send_private_msg(user_id=event.user_id, message=message)


async def send_to_group(bot: Bot, event: GroupMessageEvent, message: str):
    """发送消息到指定群聊"""
    await bot.send_group_msg(group_id=event.group_id, message=message)


async def run_in_pool(func, *args, **kwargs):
    """在协程池中运行函数"""
    await semaphore.acquire()
    result = await asyncio.to_thread(func, *args, **kwargs)
    semaphore.release()
    return result


def check_cd(group_id: int):
    current_time = time.time()
    if group_id in last_request_time:
        time_diff = current_time - last_request_time[group_id]
        if time_diff < CD_SECONDS:
            return False, CD_SECONDS - time_diff
    return True, 0


async def set_emoji(bot: Bot, event: GroupMessageEvent, emoji_id: str):
    try:
        await bot.call_api(
            "set_msg_emoji_like", message_id=event.message_id, emoji_id=emoji_id, set=True
        )
    except Exception as e:
        print(f"[jmcomic] 贴{emoji_id}表情失败：{str(e)}")


@comic.handle()
async def handle_download(
    matcher: Matcher,
    bot: Bot,
    event: GroupMessageEvent | PrivateMessageEvent,
    args: Message = CommandArg(),
):
    global whitelist, superadmin, max_image_count, ban_english, last_request_time, option, queue_started, negative_tags
    if isinstance(event, GroupMessageEvent):
        if not event.group_id in whitelist:
            return
        # 检查CD
        cd_pass, remaining_time = check_cd(event.group_id)
        if not cd_pass:
            await matcher.finish(
                f"别撸了，傻逼。{remaining_time:.1f}秒后再撸"
            )
            return
    else:
        if not event.user_id in superadmin:
            return

    # 获取命令参数（本子ID）
    args = args.extract_plain_text().strip().split(" ")
    if len(args) > 0:
        comic_id = args[0]
        if (not comic_id.isdigit()) and (comic_id != "random") and (comic_id != "help"):
            await matcher.finish("格式不正确，发送/manga help获取指令使用方式")
            return
        elif comic_id == "help":
            await matcher.finish(
                Message([MessageSegment.text("解压密码: jm + 本子ID，如jm123456"), MessageSegment.image(Path(__file__).parent / "help.png")])
            )
            return
    else:
        await matcher.finish("格式不正确，发送/manga help获取指令使用方式")

    # 贴一个OK表情提示已开始处理请求
    if isinstance(event, GroupMessageEvent):
        await set_emoji(bot, event, "124")

    client = option.build_jm_client()

    if comic_id == "random":
        try:
            conn = sqlite3.connect(os.path.join(script_dir, "albums.db"))
            cur = conn.cursor()
            rules = []
            if len(args) > 1:
                try:
                    for i in range(1, len(args)):
                        arg_name, arg_exp = args[i].split("=")
                        rules.append(RuleLogicExpression(arg_name, arg_exp))
                except Exception as e:
                    await matcher.finish("标签表达式错误：" + str(e))
                    return
            rules_sql = ""
            for rule in rules:
                rules_sql += f" AND ({rule.to_sql()})"
            command = (
                f"SELECT id FROM albums WHERE (page_count <= {max_image_count})"
                + (
                    " AND (length(tags) <> length(cast(tags AS blob)))"
                    if ban_english
                    else ""
                )
                + rules_sql
                + (f" AND ({negtag_to_sql(negative_tags)})" if negative_tags else "")
                + " ORDER BY RANDOM() LIMIT 1"
            )
            print("[jmcomic] SQL command: " + command)
            try:
                cur.execute(command)
                result = cur.fetchall()
            except Exception as e:
                print(f"[jmcomic] 数据库查询失败：{str(e)}")
                return
            if not result:
                await matcher.finish("没有符合条件的本子")
            comic_id = result[0][0]
            await matcher.send(f"随机本子ID: {comic_id}")
        finally:
            conn.close()

    try:
        album = client.get_album_detail(comic_id)
    except Exception as e:
        # 贴一个流泪表情提示处理失败
        if isinstance(event, GroupMessageEvent):
            await set_emoji(bot, event, "9")
        await matcher.finish(f"获取本子信息失败")
        return
    if album.page_count > max_image_count:
        if isinstance(event, GroupMessageEvent) or not event.user_id in superadmin:
            credit_required = math.floor(album.page_count / max_image_count)
            if isinstance(event, PrivateMessageEvent):
                await matcher.finish("本子图片数量过多")
            else:
                if credits.get(str(event.group_id), 0) < credit_required:
                    # 贴一个流泪表情提示处理失败
                    await set_emoji(bot, event, "9")
                    await matcher.finish(f"本群剩余下载令牌不足，还剩{credits.get(str(event.group_id), 0)}个令牌，下载需要{credit_required}个令牌")
                else:
                    credits[str(event.group_id)] -= credit_required
                    with open(credits_path, "w", encoding="utf-8") as f:
                        json.dump(credits, f, ensure_ascii=False, indent=4)
                    await matcher.send(f"本子图片数量过多，已扣除{credit_required}个令牌，本群剩余{credits.get(str(event.group_id), 0)}个令牌")

    # 更新最后请求时间
    if isinstance(event, GroupMessageEvent):
        last_request_time[event.group_id] = time.time()

    # 创建下载任务
    task = DownloadTask(album=album, event=event)

    # 提交下载任务至线程池
    asyncio.create_task(handle_download_task(bot, task))

    await matcher.send(f"已将本子ID: {comic_id}加入下载队列")


async def handle_download_task(bot: Bot, task: DownloadTask):
    global option, info_pic, file_suffix
    await semaphore.acquire()
    file_path = os.path.join(script_dir, "compressed", f"{task.album.id}{file_suffix}")
    await asyncio.sleep(0.5)
    print(f"[jmcomic] 开始下载 {task.album.id}")
    if isinstance(task.event, GroupMessageEvent):
        await send_to_group(bot, task.event, f"开始下载本子ID: {task.album.id}")
    else:
        await send_to_user(bot, task.event, f"开始下载本子ID: {task.album.id}")

    try:
        if task.album.album_id in downloading_manga:
            while task.album.album_id in downloading_manga:
                await asyncio.sleep(1)
            if not os.path.exists(file_path):
                raise "短时间内前一个同JM号下载任务出错，放弃本次尝试"
        else:
            downloading_manga.add(task.album.album_id)
            try:
                if not os.path.exists(file_path):
                    # 开始下载图片
                    await asyncio.to_thread(packaged_download_album, option, task.album)
                    # 转化为pdf并加密压缩
                    await asyncio.to_thread(zip_manga, task.album.id, "", file_suffix)
            except Exception as e:
                if isinstance(task.event, GroupMessageEvent):
                    await send_to_group(bot, task.event, f"下载失败：{str(e)}")
                else:
                    await send_to_user(bot, task.event, f"下载失败：{str(e)}")
                print(f"[jmcomic] 下载失败：{str(e)}")
            finally:
                downloading_manga.remove(task.album.album_id)

        if not os.path.exists(
            os.path.join(os.path.dirname(__file__), f"info_image/{task.album.id}.jpg")
        ):
            try:
                # 生成封面图片
                info_pic.generate(task.album)
            except Exception as e:
                if isinstance(task.event, GroupMessageEvent):
                    await send_to_group(bot, task.event, f"生成封面图片失败")
                else:
                    await send_to_user(bot, task.event, f"生成封面图片失败")
                print(f"[jmcomic] 生成封面图片失败：{str(e)}")

        delete_temp_file(task.album.album_id)
        file_name = f"{task.album.id}{file_suffix}"
        try:
            try:
                if isinstance(task.event, GroupMessageEvent):
                    await bot.send_group_msg(
                        group_id=task.event.group_id,
                        message=[{
                            "type": "image",
                            "data": {
                                "file": f"file://{os.path.join(script_dir, f'info_image/{task.album.id}.jpg')}",
                            }
                        }],
                    )
                else:
                    await bot.send_private_msg(
                        user_id=task.event.user_id,
                        message=[{
                            "type": "image",
                            "data": {
                                "file": f"file://{os.path.join(script_dir, f'info_image/{task.album.id}.jpg')}",
                            }
                        }],
                    )
            except Exception as e:
                pass
            if isinstance(task.event, GroupMessageEvent):
                await bot.upload_group_file(
                    group_id=task.event.group_id, file=file_path, name=file_name
                )
            else:
                await bot.upload_private_file(
                    user_id=task.event.user_id, file=file_path, name=file_name
                )
        except Exception as e:
            if isinstance(task.event, GroupMessageEvent):
                await send_to_group(bot, task.event, f"文件上传失败：{str(e)}")
            else:
                await send_to_user(bot, task.event, f"文件上传失败：{str(e)}")
            print(f"[jmcomic] 上传失败，文件路径: {file_path}")
    except Exception as e:
        if isinstance(task.event, GroupMessageEvent):
            await send_to_group(bot, task.event, f"处理过程中出错：{str(e)}")
        else:
            await send_to_user(bot, task.event, f"处理过程中出错：{str(e)}")
        print(f"[jmcomic] 处理错误：{str(e)}")
    
    semaphore.release()


@suffix.handle()
async def handle_suffix(
    matcher: Matcher,
    event: GroupMessageEvent | PrivateMessageEvent,
    args: Message = CommandArg(),
):
    global file_suffix, config, config_path
    if isinstance(event, GroupMessageEvent):
        if not event.group_id in whitelist:
            return
    args = args.extract_plain_text().strip().split(" ")
    if len(args) > 0:
        new_suffix = args[0]
        if new_suffix == "" and len(args) == 1:
            await matcher.finish(f"当前发送文件后缀为：{file_suffix}")
            return
        if new_suffix[0] != ".":
            await matcher.finish("后缀必须以.开头")
            return

        # 更新全局变量
        file_suffix = new_suffix
        config["file_suffix"] = new_suffix

        # 更新.json文件
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=4)

        await matcher.finish(f"修改发送文件后缀为：{file_suffix}")
    else:
        await matcher.finish(f"当前发送文件后缀为：{file_suffix}")


@image_set.handle()
async def handle_max_images(
    matcher: Matcher,
    event: GroupMessageEvent | PrivateMessageEvent,
    args: Message = CommandArg(),
):
    global max_image_count, config, config_path
    if isinstance(event, GroupMessageEvent):
        if not event.group_id in whitelist:
            return
    args = args.extract_plain_text().strip().split(" ")
    if len(args) > 0:
        new_count = args[0]
        if new_count == "" and len(args) == 1:
            await matcher.finish(f"当前单本最大图片数量为：{max_image_count}")
            return
        if not new_count.isdigit():
            await matcher.finish("参数必须是数字")
            return

        # 更新全局变量
        max_image_count = int(new_count)
        config["max_image_count"] = int(new_count)

        # 更新.json文件
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=4)

        await matcher.finish(f"修改单本最大图片数量为：{max_image_count}")
    else:
        await matcher.finish(f"当前单本最大图片数量为：{max_image_count}")

@credit.handle()
async def handle_credit(
    matcher: Matcher,
    event: GroupMessageEvent,
    args: Message = CommandArg(),
):
    global credits
    if not event.group_id in whitelist:
        return
    args = args.extract_plain_text().strip().split(" ")
    if len(args) > 0:
        command = args[0]
        if command == "add":
            if len(args) != 2 or not args[1].isdigit():
                await matcher.finish("格式不正确")
            add_amount = int(args[1])
            credits[str(event.group_id)] = credits.get(str(event.group_id), 0) + add_amount
            with open(credits_path, "w", encoding="utf-8") as f:
                json.dump(credits, f, ensure_ascii=False, indent=4)
            await matcher.finish(f"添加了{add_amount}个令牌，当前剩余{credits.get(str(event.group_id), 0)}个令牌")
        elif command == "clear":
            credits[str(event.group_id)] = 0
            with open(credits_path, "w", encoding="utf-8") as f:
                json.dump(credits, f, ensure_ascii=False, indent=4)
            await matcher.finish("已清空本群剩余下载令牌")
        else:
            await matcher.finish("未知命令")
    await matcher.finish(f"当前本群剩余下载令牌：{credits.get(str(event.group_id), 0)}个")

@negtags.handle()
async def handle_negtags(
    matcher: Matcher,
    event: GroupMessageEvent | PrivateMessageEvent,
    args: Message = CommandArg(),
):
    global negative_tags, config, config_path
    if isinstance(event, GroupMessageEvent):
        if not event.group_id in whitelist:
            return
    args = args.extract_plain_text().strip().split(" ")
    if len(args) > 0:
        command = args[0]
        if command == "" and len(args) == 1:
            await matcher.finish(f"当前负面标签为：{negative_tags}")
            return
        if command == "clear":
            negative_tags = []
            config["negative_tags"] = []
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=4)
            await matcher.finish("已清空负面标签")
            return
        elif command == "add":
            new_tags = [zhconv.convert(tag, "zh-hans").lower() for tag in args[1:]]
            if not new_tags:
                await matcher.finish("请提供要添加的负面标签")
                return
            negative_tags.extend(new_tags)
            negative_tags = list(set(negative_tags))
            config["negative_tags"] = negative_tags
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=4)
            await matcher.finish(f"添加负面标签：{new_tags}")
            return
        elif command == "remove":
            remove_tags = [zhconv.convert(tag, "zh-hans").lower() for tag in args[1:]]
            if not remove_tags:
                await matcher.finish("请提供要移除的负面标签")
                return
            for tag in remove_tags:
                if tag in negative_tags:
                    negative_tags.remove(tag)
            config["negative_tags"] = negative_tags
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=4)
            await matcher.finish(f"移除负面标签：{remove_tags}")
            return
        else:
            await matcher.finish("未知命令")
            return
    else:
        await matcher.finish(f"当前负面标签为：{negative_tags}")


@cooldown.handle()
async def handle_cooldown(
    matcher: Matcher,
    event: GroupMessageEvent | PrivateMessageEvent,
    args: Message = CommandArg(),
):
    global CD_SECONDS
    if isinstance(event, GroupMessageEvent):
        if not event.group_id in whitelist:
            return
    args = args.extract_plain_text().strip().split(" ")
    if len(args) > 0:
        new_cd = args[0]
        if new_cd == "" and len(args) == 1:
            await matcher.finish(f"当前CD时间为：{CD_SECONDS}秒")
            return
        if not new_cd.isdigit():
            await matcher.finish("参数必须是数字")
            return

        # 更新全局变量
        CD_SECONDS = int(new_cd)
        config["cd_seconds"] = int(new_cd)

        # 更新.json文件
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=4)

        await matcher.finish(f"修改CD时间为：{CD_SECONDS}秒")
    else:
        await matcher.finish(f"当前CD时间为：{CD_SECONDS}秒")


@white_list.handle()
async def handle_whitelist(
    matcher: Matcher,
    event: GroupMessageEvent | PrivateMessageEvent,
    args: Message = CommandArg(),
):
    global whitelist, config, config_path
    if isinstance(event, GroupMessageEvent):
        if not event.group_id in whitelist:
            return
    args = args.extract_plain_text().strip().split(" ")
    if len(args) > 0:
        command = args[0]
        if command == "" and len(args) == 1:
            await matcher.finish(f"当前白名单群号为：{whitelist}")
            return

        if command == "clear":
            whitelist = []
            config["whitelist"] = []
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=4)
            await matcher.finish("已清空白名单")
            return
        elif command == "add":
            new_list = args[1:]
            if not new_list:
                await matcher.finish("请提供要添加的群号")
                return
            for i, item in enumerate(new_list):
                if not item.isdigit():
                    await matcher.finish("存在不合法群号")
                    return
                new_list[i] = int(item)
            whitelist.extend(new_list)
            whitelist = list(set(whitelist))
            config["whitelist"] = whitelist
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=4)
            await matcher.finish(f"添加白名单：{new_list}")
            return
        elif command == "remove":
            remove_list = args[1:]
            if not remove_list:
                await matcher.finish("请提供要移除的群号")
                return
            for i, item in enumerate(remove_list):
                if not item.isdigit():
                    await matcher.finish("存在不合法群号")
                    return
                remove_list[i] = int(item)
            for item in remove_list:
                if item in whitelist:
                    whitelist.remove(item)
            config["whitelist"] = whitelist
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=4)
            await matcher.finish(f"移除白名单：{remove_list}")
            return
        else:
            await matcher.finish("未知命令")
            return
    else:
        await matcher.finish(f"当前白名单为：{whitelist}")


@baneng.handle()
async def handle_baneng(
    matcher: Matcher,
    event: GroupMessageEvent | PrivateMessageEvent,
    args: Message = CommandArg(),
):
    global ban_english
    if isinstance(event, GroupMessageEvent):
        if not event.group_id in whitelist:
            return
    args = args.extract_plain_text().strip().split(" ")
    if len(args) > 0:
        commmand = args[0]
        if commmand == "" and len(args) == 1:
            await matcher.finish(f"当前是否排除欧美本：{ban_english}")
            return
        if commmand == "set":
            ban_english = True
            config["ban_english"] = True
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=4)
            await matcher.finish(f"已设置为排除欧美本")
        elif commmand == "unset":
            ban_english = False
            config["ban_english"] = False
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=4)
            await matcher.finish(f"已设置为不排除欧美本")
        else:
            await matcher.finish(f"未知命令")
    else:
        await matcher.finish(f"当前是否排除欧美本：{ban_english}")


# 获取定时任务调度器
scheduler = require("nonebot_plugin_apscheduler").scheduler

'''
    Add cron task here to automatically update the database.
'''


async def update_database():
    """定时更新数据库的任务"""
    global is_updating_db, db_utils

    if is_updating_db:
        print("[jmcomic] 数据库正在更新中，跳过本次更新")
        return

    try:
        is_updating_db = True
        print("[jmcomic] 开始更新数据库...")
        await asyncio.to_thread(db_utils.update_db)
        print("[jmcomic] 数据库更新完成")
    except Exception as e:
        print(f"[jmcomic] 数据库更新出错: {str(e)}")
    finally:
        is_updating_db = False
        db_utils.is_update = False

@updatedb.handle()
async def handle_updatedb(
    matcher: Matcher,
    event: GroupMessageEvent | PrivateMessageEvent,
    args: Message = CommandArg(),
):
    global is_updating_db, db_utils
    if isinstance(event, GroupMessageEvent):
        if not event.group_id in whitelist:
            return

    if is_updating_db:
        await matcher.finish("数据库正在更新中，请稍后再试")
        return

    try:
        is_updating_db = True
        args = args.extract_plain_text().strip().split(" ")
        if (len(args) == 1) and (args[0] == ""):
            await matcher.send("开始更新数据库...")
            await asyncio.to_thread(db_utils.update_db)
            await matcher.send("数据库更新完成")
        else:
            for i, item in enumerate(args):
                if not item.isdigit():
                    is_updating_db = False
                    await matcher.finish("存在不合法ID")
                    return
                args[i] = int(item)
            await matcher.send("开始更新数据库...")
            await asyncio.to_thread(db_utils.update_db_listed, args)
            await matcher.send("数据库更新完成")
    except Exception as e:
        is_updating_db = False
        db_utils.is_update = False
        await matcher.finish(f"数据库更新出错: {str(e)}")
    finally:
        is_updating_db = False
        db_utils.is_update = False