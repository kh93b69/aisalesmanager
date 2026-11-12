"use client";

// Настройки → Картинки: бот может отправлять их клиентам (прайс, схема проезда и т.п.).

import { useEffect, useRef, useState } from "react";
import { apiDelete, apiPut, apiRequest, Bot, BotImage, errorText } from "../api";

type Props = {
  bot: Bot;
};

export default function ImagesSection({ bot }: Props) {
  const [images, setImages] = useState<BotImage[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const loadImages = () => {
    setLoading(true);
    apiRequest<{ images: BotImage[] }>(`/api/bots/${bot.id}/images`)
      .then((data) => setImages(data.images || []))
      .catch((err) => setError(errorText(err)))
      .finally(() => setLoading(false));
  };

  // Первая загрузка. cancelled — чтобы ответ для старого бота не попал к новому
  useEffect(() => {
    let cancelled = false;
    setError("");
    setImages([]);
    setLoading(true);
    apiRequest<{ images: BotImage[] }>(`/api/bots/${bot.id}/images`)
      .then((data) => {
        if (!cancelled) setImages(data.images || []);
      })
      .catch((err) => {
        if (!cancelled) setError(errorText(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [bot.id]);

  const upload = async (file: File) => {
    setUploading(true);
    setError("");
    const formData = new FormData();
    formData.append("file", file);
    try {
      await apiRequest(`/api/bots/${bot.id}/images`, { method: "POST", body: formData });
      loadImages();
    } catch (err) {
      setError(errorText(err));
    }
    setUploading(false);
    if (fileRef.current) fileRef.current.value = "";
  };

  // Описание картинки: по нему бот решает, когда её отправить
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [savedId, setSavedId] = useState("");

  const saveName = async (image: BotImage) => {
    const name = (drafts[image.id] ?? image.name).trim();
    if (!name || name === image.name) return;
    setError("");
    try {
      const data = await apiPut<BotImage>(`/api/images/${image.id}`, { name });
      setImages((prev) => prev.map((i) => (i.id === image.id ? { ...i, name: data.name } : i)));
      setSavedId(image.id);
    } catch (err) {
      setError(errorText(err));
    }
  };

  const remove = async (image: BotImage) => {
    if (!confirm(`Удалить картинку «${image.name}»? Бот больше не сможет её отправлять.`)) return;
    setError("");
    try {
      await apiDelete(`/api/images/${image.id}`);
      setImages((prev) => prev.filter((i) => i.id !== image.id));
    } catch (err) {
      setError(errorText(err));
    }
  };

  return (
    <div className="settings-section">
      <h3>Картинки</h3>
      <p>Бот сможет отправлять эти картинки клиентам: кейсы, отзывы, примеры работ, прайс, схема проезда.
        Под каждой картинкой напишите, что на ней и когда её отправлять — <b>по этому описанию бот выбирает картинку</b>.
        Например: «Кейс: магазин ёлок, 300 заявок за месяц — когда просят кейсы или результаты».</p>

      <div className={`upload-area ${uploading ? "disabled" : ""}`} onClick={() => !uploading && fileRef.current?.click()}>
        <div style={{ fontSize: 28, marginBottom: 6, opacity: 0.5 }}>{uploading ? "⏳" : "+"}</div>
        <div style={{ fontSize: 14 }}>{uploading ? "Загрузка…" : "Загрузить картинку"}</div>
        <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 4 }}>JPG, PNG, WEBP</div>
        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) upload(file);
          }}
        />
      </div>

      {error && <div className="alert alert-error" style={{ marginTop: 12 }}>{error}</div>}

      {loading && images.length === 0 ? (
        <div className="text-muted" style={{ marginTop: 12 }}><span className="spinner" /> Загружаем…</div>
      ) : images.length === 0 ? (
        <div className="empty-box" style={{ marginTop: 12 }}>Картинок пока нет</div>
      ) : (
        <div className="image-grid">
          {images.map((img) => (
            <div key={img.id || img.url} className="image-tile">
              <a href={img.url} target="_blank" rel="noreferrer">
                <img src={img.url} alt={img.name} />
              </a>
              <textarea
                className="input image-description"
                rows={3}
                maxLength={300}
                value={drafts[img.id] ?? img.name}
                onChange={(e) => {
                  setDrafts({ ...drafts, [img.id]: e.target.value });
                  setSavedId("");
                }}
                onBlur={() => saveName(img)}
                placeholder="Что на картинке и когда отправлять"
                title="Описание сохраняется, когда вы уходите из поля"
              />
              {savedId === img.id && <div className="text-success image-saved">✓ Сохранено</div>}
              {img.id && (
                <button className="image-delete" onClick={() => remove(img)} title="Удалить">×</button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
