/**
 * ModelSelectorMini — 紧凑型 LLM 模型选择器
 *
 * 用于 DataCenterCopilot / Pilot 面板的 Header 区域，自行加载
 * GET /api/v1/llm-configs?enabled_only=true 并渲染一个小 Select。
 */
import React, { useEffect, useState } from 'react';
import { Select, Tooltip } from 'antd';

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL as string) || '/api/v1';

interface LLMConfigItem {
  model_key: string;
  model_name: string;
  icon?: string;
  is_default: boolean;
  is_enabled: boolean;
}

interface ModelSelectorMiniProps {
  value: string;
  onChange: (key: string) => void;
  accessToken?: string | null;
}

const ModelSelectorMini: React.FC<ModelSelectorMiniProps> = ({
  value,
  onChange,
  accessToken,
}) => {
  const [configs, setConfigs] = useState<LLMConfigItem[]>([]);

  useEffect(() => {
    const headers: Record<string, string> = {};
    if (accessToken) headers['Authorization'] = `Bearer ${accessToken}`;
    fetch(`${API_BASE_URL}/llm-configs?enabled_only=true`, { headers })
      .then((r) => r.json())
      .then((json) => {
        if (json.success && Array.isArray(json.data)) {
          const enabled = json.data.filter((c: LLMConfigItem) => c.is_enabled);
          setConfigs(enabled);
          // 若当前无选中值，自动选择默认模型
          if (!value && enabled.length > 0) {
            const def = enabled.find((c: LLMConfigItem) => c.is_default) ?? enabled[0];
            onChange(def.model_key);
          }
        }
      })
      .catch(() => {/* 静默降级 */});
  // 仅在 mount 时拉取，accessToken 变化重新拉
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accessToken]);

  if (configs.length === 0) return null;

  const shortName = (name: string) =>
    name.length > 9 ? name.slice(0, 9) + '…' : name;

  return (
    <Tooltip title="切换 AI 模型" placement="bottom">
      <Select
        value={value || undefined}
        onChange={onChange}
        size="small"
        style={{ width: 130, fontSize: 12 }}
        variant="borderless"
        optionLabelProp="label"
        popupMatchSelectWidth={false}
      >
        {configs.map((c) => (
          <Select.Option
            key={c.model_key}
            value={c.model_key}
            label={
              <span style={{ fontSize: 12 }}>
                {c.icon || '🤖'} {shortName(c.model_name)}
              </span>
            }
          >
            <span style={{ fontSize: 12 }}>
              {c.icon || '🤖'} {c.model_name}
              {c.is_default && (
                <span style={{ color: '#1677ff', marginLeft: 4, fontSize: 10 }}>
                  默认
                </span>
              )}
            </span>
          </Select.Option>
        ))}
      </Select>
    </Tooltip>
  );
};

export default ModelSelectorMini;
