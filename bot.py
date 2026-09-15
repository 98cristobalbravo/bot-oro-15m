# ==============================================================================
# BOT ORO 15M (Ejecución cada 5 min con señal de vida)
# ==============================================================================

from datetime import datetime
import os
import numpy as np
import pandas as pd
import requests
import yfinance as yf

# Obtenemos las credenciales
TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def enviar_alerta(mensaje):
  url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
  payload = {"chat_id": CHAT_ID, "text": mensaje, "parse_mode": "Markdown"}
  try:
    response = requests.post(url, json=payload)
    if not response.ok:
      print(f"Error al enviar alerta a Telegram: {response.text}")
  except Exception as e:
    print(f"Excepción de red en Telegram: {e}")

def ejecutar_revision():
  # Obtenemos la hora actual en Chile
  ahora = pd.Timestamp.now(tz="America/Santiago")
  print(f"Ejecutando bot a las {ahora.strftime('%H:%M')}...")

  try:
    # 1. Descargamos datos de 15 minutos
    df = yf.download(
        "GC=F", period="5d", interval="15m", auto_adjust=False, progress=False
    )
    if isinstance(df.columns, pd.MultiIndex):
      df.columns = df.columns.get_level_values(0)

    df = df.copy()
    df.index = pd.to_datetime(df.index)
    if df.index.tz is None:
      df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert("America/Santiago")
    df.dropna(inplace=True)

    if len(df) < 30:
      print("-> Datos insuficientes.")
      return

    # 2. SEÑAL DE VIDA CADA 5 MINUTOS
    precio_actual_vivo = df["Close"].iloc[-1]
    msg_vivo = f"🤖 *BOT VIVO* | {ahora.strftime('%H:%M')} | Oro: `{precio_actual_vivo:.2f}`"
    enviar_alerta(msg_vivo)

    # 3. Cálculo de indicadores (Heikin-Ashi, RSI, ATR)
    ha = pd.DataFrame(index=df.index)
    ha["Open"] = df["Open"]
    ha["High"] = df["High"]
    ha["Low"] = df["Low"]
    ha["Close"] = df["Close"]

    ha["HA_Close"] = (df["Open"] + df["High"] + df["Low"] + df["Close"]) / 4
    ha["HA_Open"] = 0.0
    ha.iloc[0, ha.columns.get_loc("HA_Open")] = (df["Open"].iloc[0] + df["Close"].iloc[0]) / 2
    for i in range(1, len(df)):
      ha.iloc[i, ha.columns.get_loc("HA_Open")] = (ha["HA_Open"].iloc[i - 1] + ha["HA_Close"].iloc[i - 1]) / 2

    ha["HA_High"] = pd.concat([df["High"], ha["HA_Open"], ha["HA_Close"]], axis=1).max(axis=1)
    ha["HA_Low"] = pd.concat([df["Low"], ha["HA_Open"], ha["HA_Close"]], axis=1).min(axis=1)
    ha["Is_Green"] = ha["HA_Close"] >= ha["HA_Open"]

    # RSI 14
    delta = ha["HA_Close"].diff()
    ganancia = delta.clip(lower=0)
    perdida = -delta.clip(upper=0)
    media_ganancia = ganancia.ewm(com=13, adjust=False).mean()
    media_perdida = perdida.ewm(com=13, adjust=False).mean()
    rs = media_ganancia / media_perdida
    ha["RSI"] = 100 - (100 / (1 + rs))

    # ATR 14
    high_low = df["High"] - df["Low"]
    high_close = np.abs(df["High"] - df["Close"].shift())
    low_close = np.abs(df["Low"] - df["Close"].shift())
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    ha["ATR"] = true_range.ewm(com=13, adjust=False).mean()

    # Indecisión y Horario
    ha["Range"] = ha["HA_High"] - ha["HA_Low"]
    ha["Body"] = abs(ha["HA_Close"] - ha["HA_Open"])
    ha["Is_Indecision"] = (ha["Body"] <= (ha["Range"] * 0.20)) & (ha["Range"] > 0)
    ha["Prev_Indecision"] = ha["Is_Indecision"].shift(1).fillna(False)

    tiempo_decimal = ha.index.hour + ha.index.minute / 60.0
    ha["En_Horario"] = (tiempo_decimal >= 7.0) & (tiempo_decimal <= 14.75)

    ha["Signal_Sell"] = ha["Prev_Indecision"] & (ha["RSI"] < 50) & (~ha["Is_Green"]) & ha["En_Horario"]
    ha["Signal_Buy"] = ha["Prev_Indecision"] & (ha["RSI"] > 50) & (ha["Is_Green"]) & ha["En_Horario"]

    # 4. EVITAR SEÑALES DUPLICADAS: Buscar la última vela que YA cerró
    velas_cerradas = ha[ha.index + pd.Timedelta(minutes=15) <= ahora]
    if len(velas_cerradas) == 0:
      return

    ultima_vela = velas_cerradas.iloc[-1]
    hora_inicio_vela = ultima_vela.name
    hora_cierre_vela = hora_inicio_vela + pd.Timedelta(minutes=15)
    
    # Calculamos hace cuántos minutos cerró esta vela
    minutos_desde_cierre = (ahora - hora_cierre_vela).total_seconds() / 60.0
    
    print(f"Vela evaluada: {hora_inicio_vela.strftime('%H:%M')} (Cerró hace {minutos_desde_cierre:.1f} minutos)")

    # 5. EVALUACIÓN DE ESTRATEGIA (Solo si la vela acaba de cerrar en los últimos 7 minutos)
    if 0 <= minutos_desde_cierre < 7.0:
      if ultima_vela["En_Horario"]:
        precio_cierre_vela = ultima_vela["Close"]
        rsi_val = ultima_vela["RSI"]
        atr_val = ultima_vela["ATR"]

        if ultima_vela["Signal_Buy"]:
          sl = precio_cierre_vela - (atr_val * 1.2)
          msg = (
              f"🟢 *¡SEÑAL DE COMPRA (BUY) - ORO!* 🟢\n\n"
              f"🕒 Hora Vela: {hora_inicio_vela.strftime('%H:%M')}\n"
              f"📥 Precio Cierre: `{precio_cierre_vela:.2f}`\n"
              f"🛡️ Stop Loss: `{sl:.2f}`\n"
              f"📊 RSI: `{rsi_val:.2f}` | ATR: `{atr_val:.2f}`"
          )
          enviar_alerta(msg)
        elif ultima_vela["Signal_Sell"]:
          sl = precio_cierre_vela + (atr_val * 1.2)
          msg = (
              f"🔴 *¡SEÑAL DE VENTA (SELL) - ORO!* 🔴\n\n"
              f"🕒 Hora Vela: {hora_inicio_vela.strftime('%H:%M')}\n"
              f"📥 Precio Cierre: `{precio_cierre_vela:.2f}`\n"
              f"🛡️ Stop Loss: `{sl:.2f}`\n"
              f"📊 RSI: `{rsi_val:.2f}` | ATR: `{atr_val:.2f}`"
          )
          enviar_alerta(msg)
        else:
          print("Mercado en horario, pero sin señales de trading en este cierre.")
      else:
        print(f"⏳ Fuera de horario de operación. No se buscarán señales.")
    else:
      print("Esta vela ya fue evaluada en un ciclo anterior de 5 minutos. No se enviarán alertas repetidas.")

  except Exception as e:
    print(f"Error general en ejecución: {e}")

if __name__ == "__main__":
  ejecutar_revision()
