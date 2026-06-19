package com.instaoff.app

import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.view.animation.DecelerateInterpolator
import android.widget.ImageView
import android.widget.RelativeLayout
import androidx.appcompat.app.AppCompatActivity

class SplashActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // Premium dark background color matching the web app theme
        val darkBackgroundColor = Color.parseColor("#0F172A") 

        // Create main splash container programmatically
        val rootLayout = RelativeLayout(this).apply {
            setBackgroundColor(darkBackgroundColor)
            layoutParams = RelativeLayout.LayoutParams(
                RelativeLayout.LayoutParams.MATCH_PARENT,
                RelativeLayout.LayoutParams.MATCH_PARENT
            )
        }

        // Create PTM Logo ImageView
        val logoSize = (280 * resources.displayMetrics.density).toInt()
        val logoView = ImageView(this).apply {
            setImageResource(R.drawable.app_logo)
            scaleType = ImageView.ScaleType.FIT_CENTER
            layoutParams = RelativeLayout.LayoutParams(logoSize, logoSize).apply {
                addRule(RelativeLayout.CENTER_IN_PARENT)
            }
            
            // Set initial state for animation
            alpha = 0f
            scaleX = 0.75f
            scaleY = 0.75f
        }

        rootLayout.addView(logoView)
        setContentView(rootLayout)

        // Premium fade-in and scale-up micro-animation
        logoView.animate()
            .alpha(1f)
            .scaleX(1f)
            .scaleY(1f)
            .setDuration(1200)
            .setInterpolator(DecelerateInterpolator())
            .withEndAction {
                // Hold splash for 800ms, then launch MainActivity
                logoView.postDelayed({
                    val intent = Intent(this@SplashActivity, MainActivity::class.java)
                    startActivity(intent)
                    finish()
                    // Apply smooth fade transition between activities
                    overridePendingTransition(android.R.anim.fade_in, android.R.anim.fade_out)
                }, 800)
            }
            .start()
    }
}
