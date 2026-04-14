import unittest
from pathlib import Path


class TestChatSidebarScrollLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sidebar_code = Path("frontend/src/components/chat/ConversationSidebar.tsx").read_text(encoding="utf-8")
        cls.chat_code = Path("frontend/src/pages/Chat.tsx").read_text(encoding="utf-8")
        cls.css_code = Path("frontend/src/index.css").read_text(encoding="utf-8")

    def test_sidebar_has_dedicated_scroll_container(self):
        self.assertIn('className="conversation-sidebar-scroll"', self.sidebar_code)
        self.assertIn("overflowY: 'auto'", self.sidebar_code)

    def test_sidebar_root_and_panes_allow_flex_shrink(self):
        self.assertIn('className="conversation-sidebar-root"', self.sidebar_code)
        self.assertIn("minHeight: 0", self.sidebar_code)
        self.assertIn('className="conversation-sidebar-pane"', self.sidebar_code)

    def test_tabs_have_scroll_compat_class(self):
        self.assertIn('className="conversation-sidebar-tabs"', self.sidebar_code)
        self.assertIn(".conversation-sidebar-tabs .ant-tabs-content-holder", self.css_code)
        self.assertIn("overflow: hidden;", self.css_code)

    def test_chat_page_keeps_sidebar_chain_shrinkable(self):
        self.assertIn("height: '100vh', minHeight: 0, overflow: 'hidden'", self.chat_code)
        self.assertIn("minHeight: 0,", self.chat_code)

    def test_chat_sider_children_are_forced_to_flex_column(self):
        self.assertIn('className="chat-page-sider"', self.chat_code)
        self.assertIn(".chat-page-sider .ant-layout-sider-children", self.css_code)
        self.assertIn("display: flex;", self.css_code)
        self.assertIn("flex-direction: column;", self.css_code)


if __name__ == "__main__":
    unittest.main()
