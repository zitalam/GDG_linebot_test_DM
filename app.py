import os
from dotenv import load_dotenv
from flask import Flask, request, abort
from linebot.v3.webhook import WebhookHandler, Event
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging. models import TextMessage
from linebot import LineBotApi, WebhookHandler
from linebot.models import (
    MessageEvent, 
    TextMessage, 
    TextSendMessage,
    ImageSendMessage)
from linebot.exceptions import InvalidSignatureError
import logging
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

# 加載 .env 文件中的變數
load_dotenv()

# 從環境變數中讀取 LINE 的 Channel Access Token 和 Channel Secret
line_token = os.getenv('LINE_TOKEN')
line_secret = os.getenv('LINE_SECRET')

# 檢查是否設置了環境變數
if not line_token or not line_secret:  
    print(f"LINE_TOKEN:   {line_token}")
    print(f"LINE_SECRET:  {line_secret}")
    raise ValueError("LINE_TOKEN 或 LINE_SECRET 未設置")

# 初始化 LineBotApi 和 WebhookHandler
line_bot_api = LineBotApi(line_token)
handler = WebhookHandler(line_secret)

# 創建 Flask 應用
app = Flask(__name__)
app.logger.setLevel(logging.DEBUG)

# ===== 網站資訊配置 =====
WEBSITE_URL = "https://pumentea.vercel.app/"

# 具體頁面 URLs（請根據你的網站結構調整）
PAGE_URLS = {
    'home': 'https://pumentea.vercel.app/',
    'menu': 'https://pumentea.vercel.app/prodcuts',  # menu 和 products 使用相同地址
    'products': 'https://pumentea.vercel.app/prodcuts',  # menu 和 products 使用相同地址
    'about': 'https://pumentea.vercel.app/about',  # 如果是獨立頁面改為 /about
    'contact': 'https://pumentea.vercel.app/store',  # 如果是獨立頁面改為 /contact
}

# LINE 自動回覆處理的關鍵字（這些訊息不由 bot 回應）
AUTO_REPLY_KEYWORDS = [
    # 營業時間相關
    '營業時間', 'opening time', 'opening hours', '幾點', '開店', '關門',
    '今天有開', '有開嗎', '開門', '營業', '休息', '公休', '開到幾點',
    '什麼時候開', '幾點開', '幾點關',
    
    # 地址相關
    '地址', 'address', 'location', '在哪裡', '在哪', 'where',
    '位置', '怎麼去', '如何到達', '店在哪', '怎麼走'
]

# 簡單的快取機制（避免每次請求都抓取網站）
website_cache = {
    'data':  None,
    'timestamp':  0
}
CACHE_DURATION = 3600  # 1小時快取

def fetch_website_info():
    """抓取網站內容"""
    import time
    current_time = time.time()
    
    # 檢查快取
    if website_cache['data'] and (current_time - website_cache['timestamp']) < CACHE_DURATION:
        app.logger.info("使用快取的網站資訊")
        return website_cache['data']
    
    try:
        app.logger.info(f"正在抓取網站內容:   {WEBSITE_URL}")
        response = requests.get(WEBSITE_URL, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # 提取網站基本資訊
        info = {
            'title': '',
            'description': '',
            'products': [],
            'links': [],
            'text_content': ''
        }
        
        # 獲取標題
        title_tag = soup.find('title')
        if title_tag:  
            info['title'] = title_tag.text.strip()
        
        # 獲取描述
        description_tag = soup.find('meta', {'name': 'description'})
        if description_tag and description_tag.get('content'):
            info['description'] = description_tag['content']
        
        # 獲取所有文字內容（用於關鍵字搜尋）
        body = soup.find('body')
        if body:
            # 移除 script 和 style 標籤
            for script in body(['script', 'style']):
                script.decompose()
            info['text_content'] = body.get_text(separator=' ', strip=True)
        
        # 嘗試找產品相關資訊
        # 方法1: 尋找包含價格符號的元素
        price_elements = soup.find_all(string=lambda text: text and ('NT$' in text or '$' in text or '元' in text))
        for elem in price_elements[: 10]:  # 最多取10個
            parent = elem.parent
            if parent:  
                product_text = parent.get_text(strip=True)
                if len(product_text) < 200:  # 避免取到太長的文字
                    info['products'].append(product_text)
        
        # 方法2: 尋找標題標籤（可能是產品名稱）
        headings = soup.find_all(['h1', 'h2', 'h3', 'h4'])
        for heading in headings[: 10]:  
            heading_text = heading.get_text(strip=True)
            if heading_text and len(heading_text) < 100:
                info['products'].append(heading_text)
        
        # 獲取重要連結
        links = soup. find_all('a', href=True)
        for link in links[:5]:  
            link_text = link.get_text(strip=True)
            link_url = urljoin(WEBSITE_URL, link['href'])
            if link_text:  
                info['links'].append({'text': link_text, 'url': link_url})
        
        # 更新快取
        website_cache['data'] = info
        website_cache['timestamp'] = current_time
        
        app.logger.info(f"成功抓取網站資訊: {len(info['products'])} 個產品項目")
        return info
        
    except requests.RequestException as e:
        app.logger.error(f"抓取網站時發生錯誤: {e}")
        return None
    except Exception as e:
        app.logger. error(f"處理網站內容時發生錯誤: {e}")
        return None

def search_in_website(keyword, website_info):
    """在網站內容中搜尋關鍵字"""
    if not website_info:  
        return None
    
    keyword_lower = keyword.lower()
    results = []
    
    # 在產品中搜尋
    for product in website_info['products']:
        if keyword_lower in product.lower():
            results.append(product)
    
    # 在全文中搜尋
    if keyword_lower in website_info['text_content'].lower():
        # 找到關鍵字附近的文字
        text = website_info['text_content']
        index = text.lower().find(keyword_lower)
        if index != -1:
            start = max(0, index - 50)
            end = min(len(text), index + 100)
            context = text[start:end].strip()
            results.append(f"...  {context}...")
    
    return results[: 5]  # 最多返回5個結果

def generate_response(user_message):
    """根據使用者訊息和網站資訊生成回應"""
    message_lower = user_message.lower()
    
    # 檢查是否為自動回覆關鍵字（返回 None 讓 LINE 自動回覆處理）
    if any(keyword in message_lower for keyword in AUTO_REPLY_KEYWORDS):
        app.logger.info(f"偵測到自動回覆關鍵字，不回應:  {user_message}")
        return None
    
    # 問候語
    if any(keyword in message_lower for keyword in ['hi', 'hello', '你好', '嗨', '哈囉', 'hey', '嘿']):
        return f"👋 您好！歡迎來到普門茶品！\n\n我可以幫您：\n• 查看菜單（輸入「菜單」）\n• 搜尋產品（輸入產品名稱）\n• 了解關於我們（輸入「關於」）\n• 查詢營業時間（輸入「營業時間」）\n• 查詢地址（輸入「地址」）\n\n🌐 官網首頁：\n{PAGE_URLS['home']}"
    
    # 菜單/產品查詢 - 使用相同的 menu 地址
    elif any(keyword in message_lower for keyword in ['menu', '菜單', 'product', '產品', 'tea', '茶', '商品', '普門']):
        website_info = fetch_website_info()
        if website_info and website_info['products']:
            products_text = "\n• ".join(website_info['products'][:8])  # 顯示前8個項目
            return f"🍵 {website_info['title']}\n\n我們的產品：\n• {products_text}\n\n📋 完整菜單請訪問：\n{PAGE_URLS['menu']}"
        else:
            return f"🍵 璞門茶菜單\n\n查看完整菜單：\n{PAGE_URLS['menu']}"
    
    # 關於我們 - 返回具體 about 頁面
    elif any(keyword in message_lower for keyword in ['about', '關於', '介紹', '簡介', 'about us']):
        website_info = fetch_website_info()
        response = f"📖 關於普門茶品\n\n"
        if website_info:  
            if website_info['description']:
                response += f"{website_info['description']}\n\n"
        response += f"🔗 了解更多關於我們：\n{PAGE_URLS['about']}"
        return response
    
    # 價格查詢
    elif any(keyword in message_lower for keyword in ['price', '價格', '多少錢', 'how much', '費用']):
        website_info = fetch_website_info()
        if website_info: 
            price_items = [p for p in website_info['products'] if any(symbol in p for symbol in ['$', 'NT', '元'])]
            if price_items:
                price_text = "\n• ".join(price_items[: 5])
                return f"💰 價格資訊：\n\n• {price_text}\n\n完整價格請訪問：\n{PAGE_URLS['menu']}"
        return f"💰 價格資訊請訪問我們的菜單頁面：\n{PAGE_URLS['menu']}"
    
    # 聯絡方式
    elif any(keyword in message_lower for keyword in ['contact', '聯絡', '聯繫', 'call', '電話']):
        return f"📞 聯絡我們：\n\n請訪問聯絡頁面了解更多：\n{PAGE_URLS['contact']}\n\n或直接在 LINE 上留言，我們會盡快回覆！"
    
    # 訂購
    elif any(keyword in message_lower for keyword in ['order', '訂購', 'buy', '購買', '訂單']):
        return f"🛒 訂購方式：\n\n1. 線上訂購：{PAGE_URLS['menu']}\n2. 在 LINE 告訴我們您想要的商品\n3. 聯繫客服\n\n需要什麼協助嗎？"
    
    # 搜尋功能
    elif any(keyword in message_lower for keyword in ['搜尋', 'search', '找', '查']):
        return f"🔍 請告訴我您想搜尋什麼？\n\n例如：「烏龍茶」、「紅茶」、「價格」等\n\n或直接訪問官網：\n{WEBSITE_URL}"
    
    # 一般搜尋（當訊息不是特定命令時）
    else:  
        website_info = fetch_website_info()
        if website_info: 
            # 嘗試在網站內容中搜尋使用者的訊息
            search_results = search_in_website(user_message, website_info)
            if search_results:
                results_text = "\n\n• ".join(search_results)
                return f"🔍 找到相關資訊：\n\n• {results_text}\n\n更多詳情：\n{PAGE_URLS['menu']}"
        
        # 如果沒找到，給予友善回應
        return f"謝謝您的訊息：「{user_message}」\n\n如需了解更多，請：\n• 輸入「菜單」查看產品\n• 輸入「關於」了解我們\n• 輸入「營業時間」查詢開店時間\n• 輸入「地址」查詢店面位置\n• 訪問官網：{WEBSITE_URL}\n\n還有什麼我可以幫您的嗎？"

# 設置一個路由來處理 LINE Webhook 的回調請求
@app.route("/", methods=['POST'])
def callback():
    # 取得 X-Line-Signature 標頭
    signature = request. headers['X-Line-Signature']

    # 取得請求的原始內容
    body = request.get_data(as_text=True)
    app.logger.info(f"Request body: {body}")

    # 驗證簽名並處理請求
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:  
        abort(400)

    return 'OK'

# 健康檢查路由（Render 需要）
@app.route("/", methods=['GET'])
def health_check():
    return 'LINE Bot is running! ', 200

# 設置一個事件處理器來處理 TextMessage 事件
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event:  Event):
    if event.message.type == "text":
        user_message = event.message.text  # 使用者的訊息
        app.logger.info(f"收到的訊息: {user_message}")

        # 使用網站資訊生成回應
        reply_text = generate_response(user_message)
        
        # 只有在有回應時才發送訊息（None 表示由 LINE 自動回覆處理）
        if reply_text:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=reply_text)
            )
        else:
            app.logger.info("讓 LINE 自動回覆處理此訊息")

# 應用程序入口點
if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)




