/*
This file is part of Telegram Desktop,
the official desktop application for the Telegram messaging service.

For license and copyright information please follow this link:
https://github.com/telegramdesktop/tdesktop/blob/master/LEGAL
*/
#pragma once

#include "mtproto/dedicated_file_loader.h"

class QNetworkAccessManager;
class QNetworkReply;

namespace Core {

// AyuGram: downloads a Release asset directly, skipping the signed
// update pipeline we cannot produce packages for.
class UpdateAssetDownload final : public QObject {
public:
	using Progress = MTP::AbstractDedicatedLoader::Progress;

	UpdateAssetDownload();

	void start(const QUrl &url, const QString &fileName);
	void cancel();

	[[nodiscard]] rpl::producer<Progress> progress() const;
	[[nodiscard]] rpl::producer<QString> readyFile() const;
	[[nodiscard]] rpl::producer<> failed() const;
	[[nodiscard]] bool active() const;

private:
	void finish(const QString &path);
	void cleanup();

	std::unique_ptr<QNetworkAccessManager> _manager;
	QNetworkReply *_reply = nullptr;
	std::unique_ptr<QFile> _file;
	QString _path;

	rpl::event_stream<Progress> _progress;
	rpl::event_stream<QString> _ready;
	rpl::event_stream<> _failed;

	rpl::lifetime _lifetime;

};

// Best-effort installer: mac mounts the dmg, win self-replaces the exe.
[[nodiscard]] bool InstallDownloadedAsset(const QString &path);

} // namespace Core
