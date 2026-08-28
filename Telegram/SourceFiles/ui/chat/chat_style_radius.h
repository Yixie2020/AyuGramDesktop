/*
This file is part of Telegram Desktop,
the official desktop application for the Telegram messaging service.

For license and copyright information please follow this link:
https://github.com/telegramdesktop/tdesktop/blob/master/LEGAL
*/
#pragma once

namespace Ui {

[[nodiscard]] int BubbleRadiusSmall();
[[nodiscard]] int BubbleRadiusLarge();

[[nodiscard]] int MsgFileThumbRadiusSmall();
[[nodiscard]] int MsgFileThumbRadiusLarge();

extern const char kOptionUseSmallMsgBubbleRadius[];

// AyuGram: bubble radius override support
void SetAppliedBubbleRadius(int value);
void SetBubbleRadiusOverride(int value);
void ClearBubbleRadiusOverride();

} // namespace Ui
