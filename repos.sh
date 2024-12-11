#!/bin/bash

# git branch ahead/behind formatting
prefix="    %(color:yellow)%(upstream:trackshort)%(color:reset)"
track="branch '%(refname:short)' %(color:bold)%(upstream:track,nobracket)%(color:reset)"
format="%(if)%(upstream:track)%(then)$prefix $track%(end)"

# loop over repos
cd ~/git-repos
for dir in */; do
	cd "$dir"

	# if untracked changes or ahead/behind of remote
	if dirty=$(git status --porcelain) \
		&& aheadbehind=$(git for-each-ref --format="%(upstream:track)" refs/heads) \
		&& [[ -n "$dirty$aheadbehind" ]]; then

		# print repo name
		basename -z -s .git `git config --get remote.origin.url`
		echo -e ":"

		# print ahead/behind branches
		git for-each-ref --color=always --format="$format" refs/heads \
			| grep -v '^$' # filter out empty lines

		# print status summary
		git -c color.status=always \
			-c color.status.added=green \
			-c color.status.untracked=yellow \
			-c color.status.changed=red \
			-c color.status.unmerged=cyan \
			status --short \
			| sed 's/^/   /' # indent output
	fi
	cd ..
done
