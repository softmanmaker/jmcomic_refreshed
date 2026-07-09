# jmcomic_refreshed：一个基于nonebot与onebot API v11的禁漫下载插件
## 插件说明
比起上一代插件，大幅优化多线程能力，并增加了一些功能
## 安装
- 使用nonebot安装并配置好onebot机器人的基本框架，参考[这里](https://github.com/nonebot/nonebot2)
- 安装好geckodriver（参考[这里](https://github.com/mozilla/geckodriver)）与Firefox浏览器
- 修改bot根目录下的.env文件，添加以下代码
    ```
    SUPERUSERS=["管理员qq号1","管理员qq号2",...]
    ```
- 将本插件文件夹整个复制到bot根目录下的plugins文件夹中
- 修改本插件文件夹中的settings.json文件，各项配置含义如下
    ```json
    {
        "cd_seconds": "单个群内连续两次下载之间的最小时间间隔",
        "file_suffix": "文件后缀名，代码采用zip压缩，但可以自定义后缀以躲过一些检查（maybe?）",
        "max_image_count": "单本最大页数限制，用于防止bot资源被耗尽，需要注意，为了方便测试功能，当管理员账号在私聊中以指定id的方式下载本子时，此项检查会被绕过",
        "negative_tags": ["负面标签列表，随机选本时，带有这些标签的本子会被自动忽略，无需额外指定。标签对大小写与繁简体不敏感"],
        "ban_english": true, // true或false，指定是否屏蔽标题与标签全部是英文的本子（到底是哪个byd传了30w本欧美本）
        "whitelist": ["白名单列表，bot仅会响应在列表中的群聊"],
        "superadmin": ["管理员列表，最好与第二步中SUPERUSERS的配置相同。在我本人的PC上无法获取到.env中的设置项，所以才添加了这一项，如果没这个问题的可以考虑把代码里相关的地方改了，然后删了这个"]
    }
    ```
    这些配置中除了superadmin项，其他都支持通过指令热更改，详见[指令列表](##指令列表)
- 修改本插件文件夹中的options文件夹内的两份配置文件，参考[这里](https://github.com/hect0x7/JMComic-Crawler-Python)
- 在[这里](https://nas.ginojin.top/@s/Xww3JaRy)下载albums.db（分享码：JMCOMIC），并放到插件根目录下
- 至此，安装工作已完成，接下来正常启动bot即可
## 使用
正常启动bot，插件会被自动加载
## 指令列表
### 用户指令
|指令|必选参数|可选参数|示例|说明|
|---|---|---|---|---|
|/manga|\<id\>||/manga 350234|下载指定id的漫画，仅会检查页数限制|
|/manga|random|\<attr\>=\<exp\>|/manga random tags=萝莉&纯爱 authors=武藤|从满足条件的本子中随机选取，会检查所有限制。条件参数可以为空。可选的attr值有`name`、`authors`和`tags`，exp则为逻辑表达式，支持`&`、`\|`、`!`、`()`四种运算符，其中attr为`tags`时为精确匹配，否则为模糊匹配，但是均对大小写与繁简体不敏感。如实例中的指令会从所有含有“萝莉”与“纯爱”两个tag，且任意画师名字中包含“武藤”两字的本子中随机选取一本|

### 管理员指令
|指令|必选参数|可选参数|示例|说明|
|---|---|---|---|---|
|/manga_suffix||\<suffix\>|/suffix .zip|修改上传文件后缀，没有可选参数时输出当前配置|
|/manga_cooldown||\<cd\>|/cooldown 5|修改单个群聊两次请求之间最小间隔，没有可选参数时输出当前配置|
|/manga_maximages||\<count\>|/maximages 200|修改单个本子最大页数限制，没有可选参数时输出当前配置|
|/manga_baneng||set / unset|/baneng set|选择是否排除纯英文本，没有可选参数时输出当前配置|
|/manga_whitelist||\<op\> \<id1\> \<id2\> ...|/whitelist add 123456 654321|操作白名单。可选的op值有`add`（添加白名单群号）、`remove`（删除白名单群号）、`clear`（清空白名单）。操作为add或remove时，需要在后面附加要操作的群号|
|/manga_negtag||\<op\> \<tag1\> \<tag2\> ...|/negtag add yaoi 耽美|操作负面标签。可选的op值有`add`（添加负面标签）、`remove`（删除负面标签）、`clear`（清空负面标签）。操作为add或remove时，需要在后面附加要操作的负面标签|
|/manga_credit|add \<num\> / clear||/manga_credit add 10|只能在群里使用，添加本群令牌数。令牌可以用于下载页数超过最大页数限制的本子，下载时消耗的令牌数为本子页数/最大页数限制（向下取整）|


## 注意事项
本插件在发布时阉割了一些功能，但没有把相关代码完全去除，各位进行少量修改即可启用
- 自动更新功能
    为了支持高级表达式与获得更高的效率，本插件将禁漫本子数据爬取到本地，并用数据库进行管理。为了随时获得新上传的本子信息，应当定时进行数据库更新。为了防止萌新滥用此功能给禁漫服务器带来太大压力，上传的版本保留了数据库更新函数，但是删掉了设置定时任务的代码，请参照nonebot说明文档启用此功能
- 文件加密功能
    目前本子文件的上传采用无加密压缩包，这有较大的概率被腾讯检测到并封号。但为了防止萌新直接使用默认密码配置，导致腾讯通过SHA查封文件，上传的版本留下了加密的接口，但是删去了其具体实现，请参照Python中一些支持加密的压缩库（如pyzipper）的说明实现加密

## 致谢
[nonebot2](https://github.com/nonebot/nonebot2)
[JMComic-Crawler-Python](https://github.com/hect0x7/JMComic-Crawler-Python)