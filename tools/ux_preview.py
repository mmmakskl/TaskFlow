"""Local, disposable mobile harness. Real handlers/FSM, mocked Telegram transport.

TEST_DATABASE_URL must end in _test. Fixture resets only that database's schema.
Run in the isolated test image; never expose this developer server publicly.
"""

import asyncio
from contextlib import asynccontextmanager
from typing import Literal, Self

from aiogram.methods import AnswerCallbackQuery
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, model_validator

from tests.conftest import factory as factory_fixture
from tests.conftest import settings as settings_fixture
from tests.integration.test_web_and_bot import BotDriver


@asynccontextmanager
async def lifespan(app):
    settings = settings_fixture.__wrapped__()
    fixture = factory_fixture.__wrapped__(settings)
    factory = await anext(fixture)
    app.state.driver = BotDriver(factory, settings)
    app.state.lock = asyncio.Lock()
    for user in (1001, 1002):
        await app.state.driver.text(user, "/start")
    yield
    await fixture.aclose()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)


class Input(BaseModel):
    user: Literal[1001, 1002] = 1001
    text: str | None = None
    callback: str | None = None

    @model_validator(mode="after")
    def one_action(self) -> Self:
        if (self.text is None) == (self.callback is None):
            raise ValueError("Choose text or callback")
        return self


def snapshot(user, start=0):
    driver = app.state.driver
    screen = driver.screen(user)
    alerts = [
        call.text
        for call in driver.api.calls[start:]
        if isinstance(call, AnswerCallbackQuery) and call.text
    ]
    return {
        "text": screen.text,
        "keyboard": screen.reply_markup.model_dump(mode="json"),
        "alerts": alerts,
    }


@app.get("/screen")
async def screen(user: Literal[1001, 1002] = 1001):
    return snapshot(user, len(app.state.driver.api.calls))


@app.post("/action")
async def action(body: Input):
    async with app.state.lock:
        start = len(app.state.driver.api.calls)
        if body.callback is not None:
            await app.state.driver.press(body.user, data=body.callback)
        else:
            await app.state.driver.text(body.user, body.text)
        return snapshot(body.user, start)


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(PAGE)


PAGE = r"""<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TaskFlow — mobile UX preview</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#e9edf1;font:16px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#1d2733} .notice{margin:16px auto;max-width:420px;font-size:13px;line-height:1.4;color:#526170;padding:0 12px}.phone{width:min(390px,100%);height:844px;max-height:calc(100dvh - 84px);margin:auto;background:#d8e4d3;display:flex;flex-direction:column;box-shadow:0 8px 32px #20302024;border-radius:20px;overflow:hidden}.header{background:#fff;padding:12px 16px;border-bottom:1px solid #cdd5da;display:flex;align-items:center;gap:10px}.avatar{border-radius:50%;background:#5594c9;color:white;width:38px;height:38px;display:grid;place-items:center}.title{font-weight:600;flex:1}.sub{font-weight:400;font-size:12px;color:#71808f;margin-top:2px}.switch{border:0;background:#e7eff7;border-radius:12px;padding:8px;min-height:44px;color:#27679d;font:inherit;font-size:13px}.chat{overflow:auto;flex:1;padding:12px 9px}.bubble{background:#fff;padding:12px;border-radius:14px 14px 14px 4px;white-space:pre-wrap;line-height:1.35;overflow-wrap:anywhere;font-size:15px}.keys{display:flex;flex-direction:column;gap:4px;margin-top:5px}.row{display:flex;gap:4px}.key{flex:1;min-width:0;min-height:44px;padding:9px 3px;border:0;border-radius:7px;background:#f4f8f1e8;color:#225b83;font:inherit;font-size:13px;line-height:1.2;overflow-wrap:anywhere;cursor:pointer}.key:hover{background:#fff}.key:active{background:#d2e7f4}.key:disabled{opacity:.6;cursor:wait}.input{display:flex;gap:8px;background:#fff;padding:10px}.input input{min-width:0;flex:1;padding:10px;border:1px solid #d6dee5;border-radius:18px;font:inherit;font-size:15px}.send,.home{border:0;background:#e8f2fc;color:#267bbb;min-width:44px;min-height:44px;border-radius:50%;font:inherit}.home{font-size:20px}.toast{position:fixed;bottom:100px;left:50%;transform:translateX(-50%);background:#263a4bee;color:white;padding:12px 16px;border-radius:12px;max-width:360px;font-size:14px;z-index:3;display:none}.busy{opacity:.6}
</style><div class="notice">Локальная проверка: настоящие handlers/FSM и тестовый PostgreSQL. Telegram API имитируется; production не затрагивается.</div><main class="phone"><header class="header"><div class="avatar">✓</div><div class="title">TaskFlow<div class="sub">Приватный бот · тестовый режим</div></div><button class="switch" id="user">Макс ▾</button></header><section class="chat" id="chat"><div class="bubble" id="text"></div><div class="keys" id="keys"></div></section><form class="input" id="form"><button type="button" class="home" id="home" aria-label="Меню">⌂</button><input id="input" aria-label="Сообщение" placeholder="Сообщение" autocomplete="off"><button class="send" aria-label="Отправить">➤</button></form></main><div class="toast" id="toast"></div>
<script>
let user=1001,busy=false;const $=id=>document.getElementById(id);function draw(data){$('text').innerHTML=data.text;$('keys').replaceChildren();for(const row of data.keyboard.inline_keyboard){let line=document.createElement('div');line.className='row';for(const b of row){let key=document.createElement('button');key.className='key';key.textContent=b.text;key.onclick=()=>send({callback:b.callback_data});line.append(key)}$('keys').append(line)}$('chat').scrollTop=0;if(data.alerts.length){$('toast').textContent=data.alerts.at(-1);$('toast').style.display='block';setTimeout(()=>$('toast').style.display='none',3200)}}async function send(action){if(busy)return;busy=true;document.querySelectorAll('button').forEach(b=>b.disabled=true);try{const response=await fetch('/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user,...action})});if(!response.ok)throw new Error('Не удалось выполнить действие');draw(await response.json())}catch(error){$('toast').textContent=error.message;$('toast').style.display='block'}finally{busy=false;document.querySelectorAll('button').forEach(b=>b.disabled=false)}}$('form').onsubmit=e=>{e.preventDefault();if($('input').value.trim()){send({text:$('input').value});$('input').value=''}};$('home').onclick=()=>send({text:'/menu'});$('user').onclick=async()=>{user=user===1001?1002:1001;$('user').textContent=user===1001?'Макс ▾':'Вика ▾';draw(await(await fetch('/screen?user='+user)).json())};fetch('/screen').then(r=>r.json()).then(draw);
</script></html>"""


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=18080, access_log=False)
