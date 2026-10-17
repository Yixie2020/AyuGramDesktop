/*
This file is part of Telegram Desktop,
the official desktop application for the Telegram messaging service.

For license and copyright information please follow this link:
https://github.com/telegramdesktop/tdesktop/blob/master/LEGAL
*/
#pragma once

// For the client and a standalone check: QtCore only, no LOG or lib_base.

#include <QtCore/QString>
#include <QtCore/QStringList>

#include <compare>

namespace Core {

// WHY: a GitHub Releases tag is compared against this build, so the fourth
// segment must rank every number before every letter (7.3.1.1 < 7.3.1.10
// < 7.3.1.a), unknown shapes last, and segments past it are ignored.

inline constexpr auto kMaxNumericTweak = quint64(999'999);
inline constexpr auto kLetterTweakBase = quint64(1'000'000);
inline constexpr auto kUnknownTweak = kLetterTweakBase + 26;

struct ReleaseVersion {
	quint64 major = 0;
	quint64 minor = 0;
	quint64 patch = 0;
	quint64 tweak = 0;

	friend inline constexpr auto operator<=>(
		ReleaseVersion,
		ReleaseVersion) = default;
	friend inline constexpr bool operator==(
		ReleaseVersion,
		ReleaseVersion) = default;
};

[[nodiscard]] inline ReleaseVersion ParseReleaseVersion(const QString &raw) {
	auto text = raw;
	if (text.startsWith(u'v') || text.startsWith(u'V')) {
		text = text.mid(1);
	}
	const auto parts = text.split(u'.');
	const auto number = [](const QString &part) -> quint64 {
		auto ok = false;
		const auto value = part.toULongLong(&ok);
		return ok ? value : 0;
	};
	const auto tweak = [&] {
		const auto part = parts.value(3);
		if (part.isEmpty()) {
			return quint64(0);
		}
		if (part.size() == 1) {
			const auto ch = part.at(0).toLatin1();
			if (ch >= 'a' && ch <= 'z') {
				return kLetterTweakBase + quint64(ch - 'a');
			} else if (ch >= 'A' && ch <= 'Z') {
				return kLetterTweakBase + quint64(ch - 'A');
			}
		}
		auto value = quint64(0);
		for (const auto &ch : part) {
			const auto digit = ch.toLatin1();
			if (digit < '0' || digit > '9') {
				return kUnknownTweak;
			}
			value = value * 10 + quint64(digit - '0');
			if (value > kMaxNumericTweak) {
				value = kMaxNumericTweak;
			}
		}
		return value;
	}();
	return {
		.major = number(parts.value(0)),
		.minor = number(parts.value(1)),
		.patch = number(parts.value(2)),
		.tweak = tweak,
	};
}

} // namespace Core
