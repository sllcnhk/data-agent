import React from 'react';
import { Select, Tag, Tooltip } from 'antd';
import { CheckCircleOutlined } from '@ant-design/icons';
import type { LLMConfig } from '../../store/useChatStore';

const { Option } = Select;

interface ModelSelectorProps {
  configs: LLMConfig[];
  selectedModel: string;
  onSelect: (modelKey: string) => void;
}

const ModelSelector: React.FC<ModelSelectorProps> = ({
  configs,
  selectedModel,
  onSelect,
}) => {
  // 仅显示启用的模型
  const enabledConfigs = configs.filter((c) => c.is_enabled);

  if (enabledConfigs.length === 0) {
    return (
      <Tag color="red">
        没有可用的模型配置,请先配置模型
      </Tag>
    );
  }

  const selectedConfig = configs.find((c) => c.model_key === selectedModel);

  return (
    <Select
      value={selectedModel}
      onChange={onSelect}
      style={{ width: 200 }}
      placeholder="选择模型"
    >
      {enabledConfigs.map((config) => (
        <Option key={config.model_key} value={config.model_key}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span>{config.icon || '🤖'}</span>
            <span>{config.model_name}</span>
            {config.is_default && (
              <Tag color="blue" style={{ margin: 0, fontSize: 10 }}>
                默认
              </Tag>
            )}
          </div>
        </Option>
      ))}
    </Select>
  );
};

export default ModelSelector;
