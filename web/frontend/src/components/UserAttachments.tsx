import { useCallback, useState } from 'react';
import type { UploadedFile } from '../lib/api';
import { attachmentHref } from '../vendor/deepseek-harness/message/attachment-reference';
import { fileSizeText } from '../vendor/deepseek-harness/message/file-size';
import { ImageLightbox } from '../vendor/deepseek-harness/composer/ImageLightbox';

function UserAttachment({ file }: { file: UploadedFile }) {
  const [imageFailed, setImageFailed] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const closePreview = useCallback(() => setPreviewing(false), []);
  const href = attachmentHref(file.path);
  const name = file.name.split(/[\\/]/).at(-1) || '附件';
  const extension = name.split('.').at(-1)?.toUpperCase() || 'FILE';
  const image = /\.(png|jpe?g|gif|webp|avif|bmp)$/i.test(name) && !imageFailed;
  const size = (file as UploadedFile & { size?: number }).size;
  const content = <>
    {image && href ? <img className="user-attachment__image" src={href} alt={name}
      loading="lazy" onError={() => setImageFailed(true)} />
      : <span className="user-attachment__type" aria-hidden="true">{extension.slice(0, 6)}</span>}
    <span className="user-attachment__info">
      <strong title={name}>{name}</strong>
      <span>{href ? `${image ? '查看原图' : '打开文件'}${typeof size === 'number' ? ` · ${fileSizeText(size)}` : ''}` : '附件引用不可用'}</span>
    </span>
  </>;
  return href ? <><a className={`user-attachment${image ? ' user-attachment--image' : ''}`}
    href={href} target="_blank" rel="noreferrer" aria-label={`查看附件：${name}`}
    onClick={event => {
      if (!image || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault(); setPreviewing(true);
    }}>{content}</a>
    {previewing && <ImageLightbox src={href} alt={name} labels={{ dialog: name, close: '关闭图片预览' }} onClose={closePreview} />}</>
    : <div className="user-attachment user-attachment--unavailable">{content}</div>;
}

export default function UserAttachments({ files }: { files: UploadedFile[] }) {
  if (!files.length) return null;
  return <div className="user-attachments" role="group" aria-label="已发送素材">
    {files.map((file, index) => <UserAttachment key={`${file.id}:${index}`} file={file} />)}
  </div>;
}
