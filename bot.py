# ==============================================================================
# BOT ORO 15M - REPORTE INTELIGENTE (FUERZA BRUTA / SIEMPRE ENVÍA LA ÚLTIMA VELA)
# ==============================================================================

from datetime import datetime
import os
import numpy as np
import pandas as pd
import requests
import yfinance as yf

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
  ahora = pd.Timestamp.now(tz="America/Santiago")
  print(f"Ejecutando revisión de mercado a las {ahora.strftime('%H:%M')}...")

  try:
    TICKER = "GC=F"
    PERIODO = "5d"
    RSI_PERIOD = 14
    ATR_PERIOD = 14
    ATR_MULTIPLIER = 1.2

    pd.set_option("future.no_silent_downcasting", True)

    df = yf.download(
        TICKER, period=PERIODO, interval="15m", auto_adjust=False, progress=False
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

    # Indicadores
    ha = pd.DataFrame(index=df.index)
    ha["Open"] = df["Open"]
    ha["High"] = df["High"]
    ha["Low"] = df["Low"]
    ha["Close"] = df["Close"]

    ha["HA_Close"] = (df["Open"] + df["High"] + df["Low"] + df["Close"]) / 4
    ha["HA_Open"] = 0.0
    ha.iloc[0, ha.columns.get_loc("HA_Open")] = (
        df["Open"].iloc[0] + df["Close"].iloc[0]
    ) / 2
    for i in range(1, len(df)):
      ha.iloc[i, ha.columns.get_loc("HA_Open")] = (
          ha["HA_Open"].iloc[i - 1] + ha["HA_Close"].iloc[i - 1]
      ) / 2

    ha["HA_High"] = pd.concat(
        [df["High"], ha["HA_Open"], ha["HA_Close"]], axis=1
    ).max(axis=1)
    ha["HA_Low"] = pd.concat(
        [df["Low"], ha["HA_Open"], ha["HA_Close"]], axis=1
    ).min(axis=1)
    ha["Is_Green"] = ha["HA_Close"] >= ha["HA_Open"]

    delta = ha["HA_Close"].diff()
    ganancia = delta.clip(lower=0)
    perdida = -delta.clip(upper=0)
    media_ganancia = ganancia.ewm(com=RSI_PERIOD - 1, adjust=False).mean()
    media_perdida = perdida.ewm(com=RSI_PERIOD - 1, adjust=False).mean()
    rs = media_ganancia / media_perdida
    ha["RSI"] = 100 - (100 / (1 + rs))

    high_low = df["High"] - df["Low"]
    high_close = np.abs(df["High"] - df["Close"].shift())
    low_close = np.abs(df["Low"] - df["Close"].shift())
    true_range = pd.concat(
        [high_low, high_close, low_close], axis=1
    ).max(axis=1)
    ha["ATR"] = true_range.ewm(com=ATR_PERIOD - 1, adjust=False).mean()

    ha["Range"] = ha["HA_High"] - ha["HA_Low"]
    ha["Body"] = abs(ha["HA_Close"] - ha["HA_Open"])
    ha["Is_Indecision"] = (ha["Body"] <= (ha["Range"] * 0.20)) & (
        ha["Range"] > 0
    )
    ha["Prev_Indecision"] = ha["Is_Indecision"].shift(1).fillna(False)

    tiempo_decimal = ha.index.hour + ha.index.minute / 60.0
    ha["En_Horario"] = pd.Series(
        (tiempo_decimal >= 7.0) & (tiempo_decimal <= 14.75), index=ha.index
    )

    ha["Signal_Sell"] = (
        ha["Prev_Indecision"]
        & (ha["RSI"] < 50)
        & (~ha["Is_Green"])
        & ha["En_Horario"]
    )
    ha["Signal_Buy"] = (
        ha["Prev_Indecision"]
        & (ha["RSI"] > 50)
        & (ha["Is_Green"])
        & ha["En_Horario"]
    )

    velas_cerradas = ha[ha.index + pd.Timedelta(minutes=15) <= ahora]
    if len(velas_cerradas) == 0:
      return

    # Tomamos siempre la última vela cerrada sin restricciones de minutos
    ultima_vela = velas_cerradas.iloc[-1]
    hora_inicio_vela = ultima_vela.name
    hora_cierre_vela = hora_inicio_vela + pd.Timedelta(minutes=15)
    
    precio_cierre = ultima_vela["Close"]
    rsi_val = ultima_vela["RSI"]
    atr_val = ultima_vela["ATR"]
    
    estado_horario = "🟢 ABIERTO (En Horario)" if ultima_vela["En_Horario"] else "🔴 CERRADO (Fuera de horario)"

    msg = f"📊 *INFORMACIÓN: ORO 15M (Vela {hora_inicio_vela.strftime('%H:%M')} - {hora_cierre_vela.strftime('%H:%M')})*\n\n"
    msg += f"🏢 *Estado:* {estado_horario}\n"
    msg += f"📌 *Precio Cierre:* `{precio_cierre:.2f}`\n"
    msg += f"⚖️ *RSI:* `{rsi_val:.2f}` | *ATR (1.2x):* `{atr_val:.2f}`\n"
    msg += f"🕯️ *Indecisión Previa:* {'Sí' if ultima_vela['Prev_Indecision'] else 'No'} | *HA Verde:* {'Sí' if ultima_vela['Is_Green'] else 'No'}\n\n"

    if ultima_vela["Signal_Buy"]:
      sl = precio_cierre - (atr_val * ATR_MULTIPLIER)
      msg += f"🟢 *PRECIO ENTRADA:* `{precio_cierre:.2f}`\n"
      msg += f"🛡️ *SL:* `{sl:.2f}`\n\n"
      msg += f"✅ *- RESUMEN - ENTRADA (COMPRA) -*"
    elif ultima_vela["Signal_Sell"]:
      sl = precio_cierre + (atr_val * ATR_MULTIPLIER)
      msg += f"🟢 *PRECIO ENTRADA:* `{precio_cierre:.2f}`\n"
      msg += f"🛡️ *SL:* `{sl:.2f}`\n\n"
      msg += f"✅ *- RESUMEN - ENTRADA (VENTA) -*"
    else:
      msg += f"⏸️ *- RESUMEN - SIN SEÑAL -"

    enviar_alerta(msg)
    print("¡Alerta enviada a Telegram con éxito!")

  except Exception as e:
    print(f"Error general en ejecución: {e}")

if __name__ == "__main__":
  ejecutar_revision()
